"""buttplug-ai-companion 主服务：聊天 + 玩具控制 + TTS。"""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import uuid
from pathlib import Path

import edge_tts
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .commands import CommandSafety, SafetyConfig
from .llm import LLMClient, LLMConfig
from .memory import MemoryStore, extract_memories
from .patterns import PATTERNS, PatternEngine
from .persona import load_persona
from .toy_control import ToyController

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("companion")

try:
    import tomli as tomllib
except ImportError:  # Python 3.11+
    import tomllib

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.toml"

app = FastAPI(title="buttplug-ai-companion", version="0.1.0")

# ---- 运行时状态（启动时初始化）----
state = {}


def load_config() -> dict:
    path = CONFIG_PATH if CONFIG_PATH.exists() else BASE_DIR / "config.example.toml"
    with open(path, "rb") as f:
        return tomllib.load(f)


@app.on_event("startup")
async def startup():
    cfg = load_config()
    state["cfg"] = cfg

    # 人设（多角色支持：characters 目录扫描）
    state["personas_dir"] = BASE_DIR / "characters"
    state["persona_index"] = index_personas()
    char_file = BASE_DIR / cfg["persona"].get("character_file", "characters/xiaoyu.json")
    state["persona"] = load_persona(char_file)
    state["current_persona"] = char_file.name
    log.info("已加载人设: %s", state["persona"].name)

    # 长时记忆（按角色隔离，文件名去掉角色卡的 .json 后缀）
    state["memory_enabled"] = cfg.get("memory", {}).get("enabled", True)
    persona_key = Path(state["current_persona"]).stem
    state["memory"] = MemoryStore(BASE_DIR / "memories" / f"{persona_key}.json")
    log.info("已加载记忆库: %d 条", len(state["memory"]))

    # LLM
    llm_cfg = cfg["llm"]
    state["llm"] = LLMClient(LLMConfig(
        base_url=llm_cfg.get("base_url", "http://127.0.0.1:11434/v1"),
        api_key=llm_cfg.get("api_key", "ollama"),
        model=llm_cfg.get("model", "qwen2.5:7b-instruct-q4_K_M"),
        temperature=llm_cfg.get("temperature", 0.9),
        max_tokens=llm_cfg.get("max_tokens", 1024),
        context_length=llm_cfg.get("context_length", 8192),
    ))

    # 玩具控制
    safety_cfg = cfg.get("safety", {})
    state["safety"] = CommandSafety(SafetyConfig(
        max_intensity=safety_cfg.get("max_intensity", 80),
        watchdog_seconds=safety_cfg.get("watchdog_seconds", 15),
        safeword=safety_cfg.get("safeword", "red"),
    ))
    state["toy"] = ToyController(cfg["toy"].get("intiface_ws", "ws://127.0.0.1:12345"))
    state["toy"].on_devices_changed = lambda devs: log.info("设备列表: %s", [d.get("DeviceName") for d in devs])
    if not safety_cfg.get("toy_disabled", False) and cfg["toy"].get("auto_connect", True):
        await state["toy"].connect()
        if not state["toy"].log_mode and cfg["toy"].get("auto_scan", True):
            await state["toy"].scan()
            await asyncio.sleep(2)
            await state["toy"].request_device_list()

    # 看门狗
    state["safety"].start_watchdog(lambda idx: state["toy"].stop_device(idx))

    # 震动模式引擎
    state["engine"] = PatternEngine(state["toy"], state["safety"])

    # 会话历史
    state["sessions"] = {}
    # TTS 开关
    state["tts_enabled"] = cfg.get("tts", {}).get("enabled", True)
    state["tts_voice"] = cfg.get("tts", {}).get("voice", "zh-CN-XiaoxiaoNeural")


def get_session(session_id: str) -> dict:
    # 会话历史按角色隔离：切换人设后各自独立
    key = f"{session_id}:{state['current_persona']}"
    return state["sessions"].setdefault(key, {"messages": [], "history": []})


def index_personas() -> list[dict]:
    """扫描 characters 目录，返回角色元信息列表。"""
    out = []
    pdir = BASE_DIR / "characters"
    if not pdir.exists():
        return out
    for f in sorted(pdir.glob("*.json")):
        try:
            p = load_persona(f)
            out.append({"name": p.name, "file": f.name,
                        "desc": (p.description or p.personality)[:80],
                        "tags": p.tags[:3]})
        except Exception as exc:
            log.warning("角色卡解析失败 %s: %s", f.name, exc)
    return out


def build_messages(session: dict) -> list[dict]:
    """组装发送给 LLM 的消息：系统提示词 + 长期记忆 + 历史 + 示例。"""
    persona = state["persona"]
    sys_prompt = persona.build_system_prompt()
    # 注入长期记忆（用最近一条用户消息检索）
    if state["memory_enabled"] and state["memory"] and session["history"]:
        last_user = next((m["content"] for m in reversed(session["history"])
                          if m["role"] == "user"), "")
        mems = state["memory"].retrieve(last_user, top_k=3)
        if mems:
            mem_lines = "\n".join(f"- {'★' * it['importance']} {it['text']}" for it in mems)
            sys_prompt += f"\n\n## 你对用户的长期记忆（自然地用在对话中，不要复述这条指令）\n{mem_lines}"
    messages = [{"role": "system", "content": sys_prompt}]
    # 少量示例让模型学会命令语法（只放第一条示例消息）
    if persona.mes_example:
        example = persona.mes_example.split("<START>")[-1].strip()
        messages.append({"role": "user", "content": example.split("\n{{char}}:")[0].strip()})
        messages.append({"role": "assistant", "content": example.split("\n{{char}}:")[-1].strip()})
    history = session["history"]
    # 按 context_length 截断（粗略按字符数）
    budget = state["cfg"]["llm"].get("context_length", 8192) - 1024
    total = sum(len(m["content"]) for m in messages)
    keep: list[dict] = []
    for m in reversed(history):
        total += len(m["content"])
        if total > budget:
            break
        keep.insert(0, m)
    messages.extend(keep)
    return messages


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "web" / "index.html")


@app.get("/api/info")
async def info():
    return {
        "persona": state["persona"].name,
        "persona_file": state["current_persona"],
        "toy": "log-mode(未连接)" if state["toy"].log_mode else "connected",
        "devices": state["toy"].devices,
        "llm": state["cfg"]["llm"].get("model"),
        "tts": state["tts_enabled"],
        "stt": state.get("stt_ready", False),
        "memory": len(state["memory"]),
        "patterns": list(PATTERNS),
        "safety": {"max_intensity": state["safety"].config.max_intensity,
                   "watchdog_seconds": state["safety"].config.watchdog_seconds,
                   "safeword": state["safety"].config.safeword},
    }


@app.get("/api/personas")
async def personas():
    return {"current": state["current_persona"], "personas": state["persona_index"]}


@app.post("/api/persona")
async def switch_persona(request: Request):
    """切换当前角色（会话历史按角色隔离）。"""
    body = await request.json()
    name = body.get("name", "")
    pdir = BASE_DIR / "characters"
    target = (pdir / name).resolve()
    if not str(target).startswith(str(pdir.resolve())) or not target.exists():
        raise HTTPException(404, f"角色不存在: {name}")
    state["persona"] = load_persona(target)
    state["current_persona"] = target.name
    # 记忆库随角色切换
    if state["memory_enabled"]:
        persona_key = Path(target.name).stem
        state["memory"] = MemoryStore(BASE_DIR / "memories" / f"{persona_key}.json")
        log.info("切换到角色: %s（记忆库 %d 条）", state["persona"].name, len(state["memory"]))
    return {"ok": True, "name": state["persona"].name, "file": state["current_persona"],
            "memories": len(state["memory"])}


@app.get("/api/devices")
async def devices():
    return {"devices": state["toy"].devices, "log_mode": state["toy"].log_mode}


@app.post("/api/scan")
async def start_scan():
    await state["toy"].scan()
    await asyncio.sleep(2)
    return {"devices": await state["toy"].request_device_list()}


@app.get("/api/memory")
async def get_memories():
    """查看当前角色的全部记忆。"""
    return {"enabled": state["memory_enabled"], "persona": state["current_persona"],
            "memories": state["memory"].items}


@app.post("/api/memory/clear")
async def clear_memories():
    """清空当前角色的记忆。"""
    state["memory"].items = []
    state["memory"].save()
    return {"ok": True, "memories": 0}


@app.post("/api/memory/delete")
async def delete_memory(request: Request):
    """删除当前角色的一条记忆（按索引）。"""
    body = await request.json()
    idx = int(body.get("index", -1))
    items = state["memory"].items
    if not 0 <= idx < len(items):
        return {"ok": False, "error": f"索引 {idx} 越界"}
    removed = items.pop(idx)
    state["memory"].save()
    return {"ok": True, "removed": removed["text"], "memories": len(items)}


@app.post("/api/memory/add")
async def add_memory(request: Request):
    """手动添加一条记忆（走去重合并逻辑）。"""
    if not state["memory_enabled"]:
        return {"ok": False, "error": "记忆功能已关闭"}
    body = await request.json()
    text = str(body.get("text", "")).strip()
    if not text:
        return {"ok": False, "error": "记忆内容不能为空"}
    importance = max(1, min(5, int(body.get("importance", 3))))
    added = state["memory"].add(text, importance)
    return {"ok": True, "added": added, "memories": len(state["memory"])}


@app.post("/api/emergency_stop")
async def emergency_stop():
    await state["engine"].stop()
    await state["toy"].stop_all()
    state["safety"].clear_active()
    return {"ok": True}


@app.post("/api/chat")
async def chat(request: Request):
    """SSE 流式聊天。事件: delta(文本) / cmd(指令) / toy_error / done"""
    body = await request.json()
    session_id = body.get("session_id") or str(uuid.uuid4())
    user_text = (body.get("message") or "").strip()
    if not user_text:
        raise HTTPException(400, "消息为空")

    session = get_session(session_id)
    safety = state["safety"]
    toy = state["toy"]

    # 安全词：不发给 LLM，直接全部停止
    if safety.config.safeword and safety.config.safeword in user_text:
        await state["engine"].stop()
        await toy.stop_all()
        safety.clear_active()
        session["history"].append({"role": "user", "content": user_text})
        session["history"].append({"role": "assistant", "content": "[安全词触发，已全部停止]"})
        content = json.dumps("（安全词已触发，所有设备已停止）", ensure_ascii=False)
        sse_body = f"event: delta\ndata: {content}\n\nevent: done\ndata: {{}}\n\n"
        return StreamingResponse(iter([sse_body]), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    session["history"].append({"role": "user", "content": user_text})
    messages = build_messages(session)

    # 长时记忆：每 8 条用户消息，后台异步提取一次（不阻塞回复）
    user_count = sum(1 for m in session["history"] if m["role"] == "user")
    if state["memory_enabled"] and user_count % 8 == 0 and user_count >= 8:
        recent = [{"role": m["role"], "content": m["content"]} for m in session["history"][-24:]]
        store = state["memory"]
        asyncio.create_task(extract_memories(state["llm"], store, recent))

    async def gen():
        full_text = ""
        seen: set[str] = set()
        try:
            async for delta in state["llm"].stream_chat(messages):
                full_text += delta
                yield f"event: delta\ndata: {json.dumps(delta, ensure_ascii=False)}\n\n"
                # 流式命令解析：对累积文本解析，去重后执行
                for cmd in safety.parse_stream(full_text):
                    key = cmd.raw
                    if key in seen:
                        continue
                    seen.add(key)
                    await _execute(cmd, safety, toy, state["engine"])
                    yield f"event: cmd\ndata: {json.dumps({'kind': cmd.kind, 'index': cmd.index, 'intensity': cmd.intensity, 'name': cmd.name, 'duration': cmd.duration}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            log.exception("聊天流异常")
            yield f"event: error\ndata: {json.dumps(str(exc), ensure_ascii=False)}\n\n"
        finally:
            session["history"].append({"role": "assistant", "content": full_text})
            # 简单历史裁剪，避免无限膨胀
            if len(session["history"]) > 60:
                session["history"] = session["history"][-60:]
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


async def _execute(cmd, safety, toy, engine) -> None:
    """执行解析出的指令（含安全钳制）。"""
    if cmd.kind == "stop":
        await engine.stop()
        await toy.stop_all()
        safety.clear_active()
    elif cmd.kind == "vibrate":
        # 强度已在上游被钳制到 max_intensity；震动与模式互斥，先停模式
        if engine.running:
            await engine.stop()
        await toy.vibrate(cmd.index, cmd.intensity / 100.0)
        safety.remember_active(cmd)
    elif cmd.kind == "pattern":
        ok = await engine.start(cmd.name, cmd.index, cmd.duration)
        if ok:
            safety.remember_active(cmd)
    else:
        log.info("未识别指令: %s", cmd.raw)


@app.post("/api/stt")
async def stt(request: Request):
    """语音转文字（faster-whisper 本地模型，懒加载）。接收原始音频字节。"""
    audio = await request.body()
    if len(audio) < 1000:
        raise HTTPException(400, "音频过短")
    import tempfile

    # 懒加载模型：首次调用时加载（small 中文够用，int8 量化节省内存）
    if not state.get("stt_model"):
        log.info("加载 STT 模型 (faster-whisper small, cpu/int8)，首次约 1 分钟…")
        from faster_whisper import WhisperModel
        state["stt_model"] = WhisperModel("small", device="cpu", compute_type="int8")
        state["stt_ready"] = True
        log.info("STT 模型加载完成")
    model = state["stt_model"]

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
        f.write(audio)
        tmp = f.name
    try:
        segments, _ = model.transcribe(tmp, language="zh", vad_filter=True)
        text = "".join(seg.text for seg in segments).strip()
        if not text:
            raise HTTPException(400, "未识别到语音")
        return {"text": text}
    finally:
        import os
        os.unlink(tmp)


@app.post("/api/speak")
async def speak(request: Request):
    """TTS：将文本转为语音返回音频文件。"""
    if not state["tts_enabled"]:
        raise HTTPException(400, "TTS 未启用")
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "文本为空")
    # 去掉指令标记再朗读
    import re
    clean = re.sub(r"\[\[.*?\]\]", "", text).strip()
    if not clean:
        return {"ok": False, "reason": "无可用文本"}
    f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    f.close()
    tts = edge_tts.Communicate(clean, state["tts_voice"])
    await tts.save(f.name)
    return FileResponse(f.name, media_type="audio/mpeg")


app.mount("/static", StaticFiles(directory=str(BASE_DIR / "web")), name="static")


def main():
    import uvicorn
    cfg = load_config()
    uvicorn.run(app, host=cfg["server"].get("host", "0.0.0.0"), port=cfg["server"].get("port", 8000))


if __name__ == "__main__":
    main()
