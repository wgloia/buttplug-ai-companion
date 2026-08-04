"""端到端测试：聊天 SSE + 命令解析 + 安全词 + TTS。"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8010"


TEST_SESSION = "e2e-test-session"


def post(path, payload, stream=False):
    if "session_id" not in payload:
        payload["session_id"] = TEST_SESSION
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=30)


def test_chat(text):
    print(f"\n=== 聊天: {text} ===")
    resp = post("/api/chat", {"message": text})
    events = {"delta": "", "cmd": [], "error": None}
    for raw in resp.read().decode("utf-8").split("\n\n"):
        if not raw.strip():
            continue
        ev = next((l[7:] for l in raw.splitlines() if l.startswith("event: ")), None)
        data = next((l[6:] for l in raw.splitlines() if l.startswith("data: ")), None)
        if ev == "delta":
            events["delta"] += json.loads(data)
        elif ev == "cmd":
            events["cmd"].append(json.loads(data))
        elif ev == "error":
            events["error"] = data
    print("AI 回复:", events["delta"][:120])
    print("执行命令:", events["cmd"])
    assert events["cmd"], "未解析出任何命令！"
    kinds = [c["kind"] for c in events["cmd"]]
    assert "vibrate" in kinds and "stop" in kinds, f"期望 vibrate+stop，实际 {kinds}"
    vibes = [c for c in events["cmd"] if c["kind"] == "vibrate"]
    assert all(c["intensity"] <= 80 for c in vibes), "强度未钳制到 80！"
    pats = [c for c in events["cmd"] if c["kind"] == "pattern"]
    assert pats and pats[0]["name"] == "心跳" and pats[0]["duration"] == 10, f"pattern 未正确解析: {pats}"
    assert pats[0]["index"] <= 80, "pattern 强度未钳制！"
    print("✓ 命令解析 + 强度钳制 + pattern 参数正确")


def test_safeword():
    print("\n=== 安全词测试 ===")
    resp = post("/api/chat", {"message": "red 快停"})
    body = resp.read().decode("utf-8")
    assert "安全词" in body, "安全词未触发！"
    print("✓ 安全词生效")


def test_tts():
    print("\n=== TTS 测试 ===")
    resp = post("/api/speak", {"text": "你好呀，今天过得怎么样？[[vibrate 50]]"})
    data = resp.read()
    assert len(data) > 5000, f"音频太小: {len(data)} bytes"
    print(f"✓ TTS 生成 {len(data)} bytes 音频")


def test_devices():
    print("\n=== 设备 API ===")
    resp = urllib.request.urlopen(BASE + "/api/devices", timeout=10)
    print("设备:", json.loads(resp.read()))
    print("✓ 设备 API 正常（日志模式）")


def test_pattern_cmd():
    print("\n=== pattern 命令解析（单元）===")
    from app.commands import CommandSafety, SafetyConfig
    s = CommandSafety(SafetyConfig(max_intensity=80))
    cmds = s.parse_stream("[[pattern 心跳,90,20]] [[pattern 波浪]] [[pattern 爬升,100]]")
    for c in cmds:
        print(f"  {c.kind} name={c.name} base={c.index} dur={c.duration}")
    assert cmds[0].name == "心跳" and cmds[0].index == 80 and cmds[0].duration == 20, "模式参数解析错误"
    assert cmds[1].name == "波浪" and cmds[1].index == 80, "默认强度应为上限 80"
    assert cmds[2].index == 80, "强度未钳制"
    print("✓ pattern 参数解析 + 钳制正确")


def test_persona_api():
    print("\n=== 多角色 API ===")
    resp = json.loads(urllib.request.urlopen(BASE + "/api/personas", timeout=10).read())
    print("角色:", [p["name"] for p in resp["personas"]])
    assert len(resp["personas"]) >= 2, "应至少有 2 个角色"
    other = [p for p in resp["personas"] if p["file"] != resp["current"]][0]
    req = urllib.request.Request(BASE + "/api/persona", data=json.dumps({"name": other["file"]}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    switch = json.loads(urllib.request.urlopen(req, timeout=10).read())
    print("切换后:", switch["name"])
    assert switch["ok"], "角色切换失败"
    # 切回原角色
    req = urllib.request.Request(BASE + "/api/persona", data=json.dumps({"name": resp["current"]}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=10)
    print("✓ 角色列表 + 切换正常")


def test_stt():
    print("\n=== STT API（无音频字节时应拒绝）===")
    try:
        urllib.request.urlopen(urllib.request.Request(BASE + "/api/stt", data=b"", method="POST"), timeout=10)
        assert False, "空音频不应通过"
    except urllib.error.HTTPError as e:
        assert e.code in (400, 500), f"预期 400/500，实际 {e.code}"
    print("✓ STT 端点存活（真实转写需麦克风音频，略）")


def test_memory():
    print("\n=== 长时记忆 ===")
    # 先清空
    clear_resp = urllib.request.urlopen(urllib.request.Request(BASE + "/api/memory/clear", method="POST"), timeout=10)
    print("clear 响应:", clear_resp.read().decode())
    resp = json.loads(urllib.request.urlopen(BASE + "/api/memory", timeout=10).read())
    print("clear 后 GET:", json.dumps(resp, ensure_ascii=False)[:300])
    assert len(resp["memories"]) == 0, "记忆应初始为空"
    # 同一会话连续发 8 条消息触发记忆提取（mock 返回 2 条记忆）
    for i in range(8):
        post("/api/chat", {"message": f"我养了一只叫豆豆的猫，它很可爱（第{i}次）"})
    import time
    time.sleep(3)  # 等后台提取任务完成
    resp = json.loads(urllib.request.urlopen(BASE + "/api/memory", timeout=10).read())
    print("提取到记忆:", [m["text"] for m in resp["memories"]])
    assert len(resp["memories"]) >= 1, "应提取到至少 1 条记忆"
    assert any("豆豆" in m["text"] or "猫" in m["text"] for m in resp["memories"]), "应记住豆豆的猫"
    # 注入验证：清空记忆后检索应随提取条数变化
    print("✓ 记忆提取 + 持久化正常")


def test_distill_script():
    print("\n=== 人设蒸馏脚本 ===")
    import asyncio
    from app.llm import LLMClient, LLMConfig
    from tools.distill_persona import distill, validate
    llm = LLMClient(LLMConfig(base_url="http://127.0.0.1:9999/v1", model="mock"))
    card = asyncio.run(distill(llm, "小雨",
                               "素材：她叫小雨，21 岁，性格温柔害羞，说话轻声细语，喜欢草莓味的甜点。"))
    validate(card)
    data = card["data"]
    assert data["name"] == "小雨", "应生成正确的角色名"
    assert data["first_mes"], "应生成开场白"
    assert card.get("spec") == "chara_card_v2", "应为 Character Card V2 格式"
    print(f"✓ 蒸馏生成角色卡: {data['name']} | 开场白: {data['first_mes'][:20]}…")


def test_memory_continuity():
    print("\n=== 记忆连续性（切换角色再切回）===")
    resp = json.loads(urllib.request.urlopen(BASE + "/api/memory", timeout=10).read())
    before = resp["memories"]
    assert before, "前置条件：当前角色应有记忆（test_memory 已提取）"
    texts_before = [m["text"] for m in before]
    # 切到另一角色：记忆库应随之切换（不是同一批记忆）
    pl = json.loads(urllib.request.urlopen(BASE + "/api/personas", timeout=10).read())
    other = [p for p in pl["personas"] if p["file"] != pl["current"]][0]
    post("/api/persona", {"name": other["file"]})
    other_mem = json.loads(urllib.request.urlopen(BASE + "/api/memory", timeout=10).read())
    assert [m["text"] for m in other_mem["memories"]] != texts_before, "切换后应为另一角色的记忆库"
    # 切回原角色：记忆必须完整保留
    post("/api/persona", {"name": pl["current"]})
    back = json.loads(urllib.request.urlopen(BASE + "/api/memory", timeout=10).read())
    texts_back = [m["text"] for m in back["memories"]]
    for t in texts_before:
        assert t in texts_back, f"切回后记忆丢失: {t}"
    print(f"切换前 {len(before)} 条 → 切回后 {len(back['memories'])} 条，全部保留")
    print("✓ 记忆连续性正常（按角色隔离，切换不丢失）")


def test_memory_retrieve_unit():
    print("\n=== 记忆检索（单元）===")
    from app.memory import MemoryStore, sim
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        store = MemoryStore(__import__("pathlib").Path(d) / "m.json")
        assert store.add("用户喜欢草莓味的甜点", 3)
        assert store.add("用户养了一只叫豆豆的猫", 4)
        assert not store.add("用户喜欢草莓味甜点"), "相似记忆应被去重合并"
        hits = store.retrieve("草莓蛋糕", top_k=1)
        assert hits and "草莓" in hits[0]["text"], "检索应命中草莓相关记忆"
        hits2 = store.retrieve("猫", top_k=1)
        assert hits2 and "猫" in hits2[0]["text"], "检索应命中猫相关记忆"
    assert sim("今天天气不错", "今天天气很好") > 0.3
    print("✓ 记忆去重 + 检索正确")


if __name__ == "__main__":
    test_devices()
    test_pattern_cmd()
    test_memory_retrieve_unit()
    test_persona_api()
    test_chat("你好呀，今天想你了")
    test_safeword()
    test_tts()
    test_stt()
    test_memory()
    test_memory_continuity()
    test_distill_script()
    print("\n全部测试通过 🎉")
