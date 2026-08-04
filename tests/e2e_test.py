"""端到端测试：聊天 SSE + 命令解析 + 安全词 + TTS。"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8010"


def post(path, payload, stream=False):
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


if __name__ == "__main__":
    test_devices()
    test_pattern_cmd()
    test_persona_api()
    test_chat("你好呀，今天想你了")
    test_safeword()
    test_tts()
    test_stt()
    print("\n全部测试通过 🎉")
