"""TTS 服务层：GPT-SoVITS 本地克隆音色（主）+ edge-tts 免费合成（回退）。

音色管理：voices/ 目录下每个 <音色名>.wav + 同名 <音色名>.txt（参考音频与对应文本），
放入即出现在音色列表，与 characters/ 目录的理念一致。
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

_CMD_RE = re.compile(r"\[\[.*?\]\]")


@dataclass
class Voice:
    name: str
    wav_path: str
    prompt_text: str = ""
    prompt_language: str = "zh"
    source: str = "gpt-sovits"  # 音色来源


@dataclass
class TTSConfig:
    engine: str = "auto"  # gpt-sovits / edge / auto（gpt-sovits 不可用时回退 edge）
    voice: str = ""
    gpt_sovits_url: str = "http://127.0.0.1:9880"
    voices_dir: str = "voices"
    edge_voice: str = "zh-CN-XiaoxiaoNeural"
    timeout: float = 60.0


class TTSManager:
    """统一 TTS 入口：优先 GPT-SoVITS 克隆音色，失败自动回退 edge-tts。"""

    def __init__(self, config: TTSConfig, base_dir: Path):
        self.config = config
        self.base_dir = base_dir
        self.voices: dict[str, Voice] = {}
        self.current: str = ""
        self._scan_voices()

    def _scan_voices(self) -> None:
        """扫描 voices 目录：<name>.wav + <name>.txt（参考文本）。"""
        vdir = self.base_dir / self.config.voices_dir
        self.voices = {}
        if vdir.exists():
            for f in sorted(vdir.glob("*.wav")):
                name = f.stem
                txt = f.with_suffix(".txt")
                prompt = txt.read_text(encoding="utf-8").strip() if txt.exists() else ""
                self.voices[name] = Voice(name=name, wav_path=str(f), prompt_text=prompt)
        # 默认音色：配置指定或第一个
        if self.config.voice and self.config.voice in self.voices:
            self.current = self.config.voice
        elif self.voices:
            self.current = next(iter(self.voices))
        log.info("TTS 音色: %s（当前: %s）", list(self.voices), self.current or "edge-tts")

    async def _gpt_sovits(self, text: str, voice: Voice | None) -> bytes:
        """调用本地 GPT-SoVITS API，返回 wav 字节。"""
        v = voice or (self.voices.get(self.current) if self.current else None)
        if not v or not v.prompt_text:
            raise RuntimeError("GPT-SoVITS 需要参考音频与参考文本（voices 目录）")
        payload = {
            "text": text,
            "text_language": "zh",
            "refer_wav_path": v.wav_path,
            "prompt_text": v.prompt_text,
            "prompt_language": v.prompt_language,
            "top_k": 15,
            "top_p": 1.0,
            "temperature": 1.0,
            "speed": 1.0,
            "sample_steps": 32,
        }
        # trust_env=False：避免系统代理劫持本地请求（用户代理环境会 502）
        async with httpx.AsyncClient(trust_env=False, timeout=self.config.timeout) as client:
            r = await client.post(self.config.gpt_sovits_url + "/", json=payload)
            r.raise_for_status()
            return r.content

    async def _edge(self, text: str) -> bytes:
        """edge-tts 合成 mp3。"""
        import edge_tts

        f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        f.close()
        tts = edge_tts.Communicate(text, self.config.edge_voice)
        await tts.save(f.name)
        data = Path(f.name).read_bytes()
        Path(f.name).unlink(missing_ok=True)
        return data

    async def speak(self, text: str) -> tuple[bytes, str]:
        """合成语音。返回 (音频字节, media_type)。engine=auto 时 gpt-sovits 失败回退 edge。"""
        clean = _CMD_RE.sub("", text).strip()
        if not clean:
            raise ValueError("无可用文本")
        if self.config.engine in ("gpt-sovits", "auto"):
            try:
                return await self._gpt_sovits(clean, None), "audio/wav"
            except Exception as exc:
                log.warning("GPT-SoVITS 合成失败: %s", exc)
                if self.config.engine == "gpt-sovits":
                    raise
        return await self._edge(clean), "audio/mpeg"

    def set_voice(self, name: str) -> bool:
        """切换当前音色（仅 GPT-SoVITS 音色）。"""
        if name in self.voices:
            self.current = name
            return True
        return False

    def voice_list(self) -> list[dict]:
        return [{"name": n, "prompt": v.prompt_text[:40]} for n, v in self.voices.items()]
