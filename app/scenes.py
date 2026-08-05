"""剧情模块：从音频提取完整对话，作为角色扮演的互动剧本。

把完整音频（如电影音轨）放进 scenes/ 目录，选中后由 faster-whisper
本地转写成文本，注入对话上下文；模型按剧本推进互动，并根据剧情中的
亲密场景触发玩具命令。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .stt import get_stt_model

log = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".webm", ".aac", ".wma", ".mp4", ".mkv", ".avi"}


class SceneManager:
    """音频扫描 + 异步转写 + 缓存。转写结果存 <音频名>.txt。"""

    def __init__(self, base_dir: Path, scenes_dir: str = "scenes",
                 stt_provider=None, language: str = "zh"):
        self.base_dir = base_dir
        self.scenes_dir = base_dir / scenes_dir
        self.scenes_dir.mkdir(exist_ok=True)
        self._stt_provider = stt_provider or get_stt_model
        self.language = language
        self._tasks: dict[str, asyncio.Task] = {}
        self.status: dict[str, str] = {}   # 音频名 -> transcribing / done / error
        self.texts: dict[str, str] = {}    # 音频名 -> 转写文本（内存缓存）

    # ---- 扫描 ----

    def list_scenes(self) -> list[dict]:
        out = []
        for f in sorted(self.scenes_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                out.append({
                    "name": f.stem,
                    "file": f.name,
                    "size": f.stat().st_size,
                    "transcribed": f.with_suffix(".txt").exists(),
                })
        return out

    def find_audio(self, name: str) -> Path | None:
        """按名称（去掉扩展名）查找音频文件。"""
        for f in self.scenes_dir.iterdir():
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS and f.stem == name:
                return f
        return None

    # ---- 转写 ----

    async def transcribe(self, name: str) -> str:
        """启动（或复用）转写任务。已有缓存则直接返回文本。"""
        txt = self.scenes_dir / f"{name}.txt"
        if txt.exists():
            text = txt.read_text(encoding="utf-8")
            self.texts[name] = text
            self.status[name] = "done"
            return text
        if name in self._tasks and not self._tasks[name].done():
            return ""   # 转写进行中
        self._tasks[name] = asyncio.create_task(self._run_transcribe(name))
        return ""

    async def _run_transcribe(self, name: str) -> str:
        self.status[name] = "transcribing"
        audio = self.find_audio(name)
        if audio is None:
            self.status[name] = "error"
            return ""
        try:
            loop = asyncio.get_event_loop()
            # 模型加载（首次约 1 分钟）与转写都在线程池执行，避免冻结事件循环
            text = await loop.run_in_executor(
                None, _transcribe_sync, self._stt_provider, str(audio), self.language)
            if not text:
                self.status[name] = "error"
                return ""
            # 缓存到磁盘与内存
            (self.scenes_dir / f"{name}.txt").write_text(text, encoding="utf-8")
            self.texts[name] = text
            self.status[name] = "done"
            log.info("剧情转写完成: %s（%d 字）", name, len(text))
            return text
        except Exception as exc:
            log.exception("转写失败 %s", name)
            self.status[name] = "error"
            return ""


def _transcribe_sync(stt_provider, path: str, language: str) -> str:
    """同步转写（在 executor 中运行）。首次调用会顺带加载模型。"""
    model = stt_provider()
    segments, _ = model.transcribe(path, language=language, vad_filter=True)
    return "".join(seg.text for seg in segments).strip()
