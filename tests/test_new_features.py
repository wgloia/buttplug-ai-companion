"""新功能单元测试：selfie 生图管线、scenes 剧情转写、llm extra_body。

不依赖运行中的服务：ComfyUI 用假 HTTP 服务器模拟，whisper 用 fake provider 注入。
"""
import json
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ============ selfie：build_workflow 纯函数 ============

def test_build_workflow():
    print("\n=== selfie build_workflow 结构（Hires Fix 两段式）===")
    from girlfriend.selfie import build_workflow
    wf = build_workflow("ponyV6XL.safetensors", "穿着睡衣", "ref.png",
                        "lowres", 0.7, sd_version="sd15")
    assert wf["5"]["inputs"]["ckpt_name"] == "ponyV6XL.safetensors", "checkpoint 应注入节点 5"
    assert wf["6"]["inputs"]["text"] == "穿着睡衣", "正向提示词应注入节点 6"
    assert wf["7"]["inputs"]["text"] == "lowres", "负向提示词应注入节点 7"
    # Hires Fix 两段式：基础采样(14) → 潜空间放大(15) → 二次采样(16) → 解码(9)
    assert wf["14"]["inputs"]["steps"] == 30, "基础段 30 步"
    assert wf["15"]["class_type"] == "LatentUpscale", "应有放大节点"
    assert wf["16"]["inputs"]["denoise"] == 0.45, "Hires 段应为低 denoise 重绘"
    assert wf["9"]["inputs"]["samples"] == ["16", 0], "VAEDecode 应取 Hires 段输出"
    # sd15：512x768 起步放大到 1024x1536
    assert wf["8"]["inputs"]["width"] == 512 and wf["8"]["inputs"]["height"] == 768
    assert wf["15"]["inputs"]["width"] == 1024 and wf["15"]["inputs"]["height"] == 1536
    # sdxl：768x1024 起步放大到 1536x2048
    wf_xl = build_workflow("ponyV6XL.safetensors", "穿着睡衣", "ref.png", "lowres", 0.7,
                           sd_version="sdxl")
    assert wf_xl["8"]["inputs"]["width"] == 768 and wf_xl["15"]["inputs"]["width"] == 1536
    # IPAdapter 三节点（加载器/参考图/应用）齐备，权重与嵌入选项正确
    assert "11" in wf and "12" in wf and "13" in wf, "应包含 IPAdapter 分支"
    assert wf["13"]["inputs"]["weight"] == 0.7
    assert wf["13"]["inputs"]["image"] == ["12", 0], "IPAdapter 应引用参考图节点"
    assert wf["13"]["inputs"].get("combine_embeds") == "concat"
    # 两段采样器模型来源均为 IPAdapter 输出
    assert wf["14"]["inputs"]["model"] == ["13", 0]
    assert wf["16"]["inputs"]["model"] == ["13", 0]
    print("✓ 两段式节点图正确（Hires Fix + IPAdapter + 分辨率自适应）")


# ============ selfie：错误路径 ============

def test_selfie_missing_checkpoint():
    print("\n=== selfie 未配置模型应报错 ===")
    from girlfriend.selfie import SelfieConfig, SelfieService
    with tempfile.TemporaryDirectory() as d:
        svc = SelfieService(SelfieConfig(checkpoint="", output_dir="web/selfies"), Path(d))
        try:
            svc.generate("测试")
            assert False, "未配置 checkpoint 应抛 RuntimeError"
        except RuntimeError as exc:
            assert "checkpoint" in str(exc)
    print("✓ 无模型配置时生成失败并给出清晰提示")


# ============ selfie：成功路径（假 ComfyUI 服务器） ============

class FakeComfyUI(BaseHTTPRequestHandler):
    """模拟 ComfyUI：/prompt 返回 prompt_id，/history 返回出图，/view 返回图片字节。"""

    last_prompt: dict = {}   # 最近一次收到的 workflow（供测试断言）

    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.path == "/prompt":
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            # 验证提交的 workflow 是合法 JSON 且包含 checkpoint
            payload = json.loads(body)
            assert "prompt" in payload and payload["prompt"]["5"]["inputs"]["ckpt_name"]
            FakeComfyUI.last_prompt = payload["prompt"]
            self._json({"prompt_id": "test-123"})
        else:
            self._json({"error": "not found"})

    def do_GET(self):
        if self.path.startswith("/history/test-123"):
            self._json({"test-123": {"outputs": {"10": {
                "images": [{"filename": "nana_selfie_00001_.png", "subfolder": "output"}]}}}})
        elif self.path.startswith("/view"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"FAKE")
        else:
            self._json({"error": "not found"})

    def _json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_selfie_success_path():
    print("\n=== selfie 完整生成链路（假 ComfyUI）===")
    from girlfriend.selfie import SelfieConfig, SelfieService
    server = HTTPServer(("127.0.0.1", 0), FakeComfyUI)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            # 参考图必须存在
            (base / "girlfriend" / "assets").mkdir(parents=True)
            ref = base / "girlfriend" / "assets" / "nana.png"
            ref.write_bytes(b"REF")
            svc = SelfieService(SelfieConfig(
                comfyui_url=f"http://127.0.0.1:{port}",
                comfyui_input_dir=str(base / "comfyui_input"),
                checkpoint="ponyV6XL.safetensors",
                reference_image="girlfriend/assets/nana.png",
                output_dir="web/selfies",
                timeout=5.0,
            ), base)
            out = svc.generate("穿着睡衣")
            assert out.exists(), "生成的图片文件应存在"
            assert out.read_bytes() == b"FAKE", "应下载到 ComfyUI 返回的图片内容"
    finally:
        server.shutdown()
    print("✓ 提交 workflow → 轮询 → 下载完整链路正常")


def test_ref_image_missing():
    print("\n=== 参考图缺失应降级为纯文生图 ===")
    from girlfriend.selfie import SelfieConfig, SelfieService
    server = HTTPServer(("127.0.0.1", 0), FakeComfyUI)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            svc = SelfieService(SelfieConfig(
                comfyui_url=f"http://127.0.0.1:{port}",
                comfyui_input_dir=str(base / "comfyui_input"),
                checkpoint="ponyV6XL.safetensors",
                reference_image="girlfriend/assets/nana.png",  # 不存在
                output_dir="web/selfies", timeout=5.0,
            ), base)
            assert svc.ref_image_path() is None, "参考图不存在时应返回 None"
            out = svc.generate("穿着睡衣")
            assert out.exists(), "纯文生图也应产出图片"
            # 降级验证：提交的 workflow 不含 IPAdapter 节点，两段采样器直连 checkpoint
            wf = FakeComfyUI.last_prompt
            assert "11" not in wf and "12" not in wf and "13" not in wf, "无参考图时应移除 IPAdapter 分支"
            assert wf["14"]["inputs"]["model"] == ["5", 0], "基础段应直连 CheckpointLoader"
            assert wf["16"]["inputs"]["model"] == ["5", 0], "Hires 段应直连 CheckpointLoader"
    finally:
        server.shutdown()
    print("✓ 参考图缺失降级为纯文生图（IPAdapter 分支移除）")


# ============ scenes：转写状态机（fake stt provider） ============

class FakeTranscriber:
    """fake faster-whisper：不真正转写，返回固定文本。"""

    def transcribe(self, path, language=None, vad_filter=False):
        return iter([SimpleNamespace(text="她轻声说：今晚月色真美")]), None


def fake_stt_provider(model_name="small"):
    return FakeTranscriber()


def test_scenes_transcribe_flow():
    print("\n=== scenes 转写状态机 ===")
    from app.scenes import SceneManager

    async def flow():
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            scenes_dir = base / "scenes"
            scenes_dir.mkdir()
            (scenes_dir / "深夜.mp3").write_bytes(b"fake-audio")
            mgr = SceneManager(base, stt_provider=fake_stt_provider)
            # transcribe 是 fire-and-forget：await 后启动后台任务立即返回
            await mgr.transcribe("深夜")
            # 单次事件循环内轮询等待任务完成（asyncio.run 结束会取消未完成任务）
            while mgr.status.get("深夜") not in ("done", "error"):
                await asyncio.sleep(0.05)
            assert mgr.status["深夜"] == "done", f"状态应为 done，实际 {mgr.status.get('深夜')}"
            assert (scenes_dir / "深夜.txt").exists(), "转写结果应缓存到磁盘"
            assert "月色真美" in mgr.texts["深夜"]
            # 二次调用：命中磁盘缓存，直接返回文本
            out = await mgr.transcribe("深夜")
            assert "月色真美" in out, "缓存命中应直接返回文本"
            # 列表扫描
            items = mgr.list_scenes()
            assert any(i["name"] == "深夜" and i["transcribed"] for i in items), "列表应标记已转写"
        print("✓ 转写 → 落盘 → 缓存命中 → 列表状态 全链路正常")

    import asyncio
    asyncio.run(flow())


def test_scenes_missing_audio():
    print("\n=== scenes 音频不存在应报错状态 ===")
    from app.scenes import SceneManager

    async def flow():
        with tempfile.TemporaryDirectory() as d:
            mgr = SceneManager(Path(d), stt_provider=fake_stt_provider)
            await mgr.transcribe("不存在的音频")
            while mgr.status.get("不存在的音频") not in ("done", "error"):
                await asyncio.sleep(0.05)
            assert mgr.status["不存在的音频"] == "error", "缺失音频应标记 error"
        print("✓ 缺失音频错误状态正常")

    import asyncio
    asyncio.run(flow())


# ============ llm：qwen3 think 关闭 / num_ctx ============

def test_llm_extra_body():
    print("\n=== llm _extra_body（Ollama num_ctx + think 关闭）===")
    from app.llm import LLMClient, LLMConfig
    ollama = LLMClient(LLMConfig(base_url="http://127.0.0.1:11434/v1", context_length=8192))
    body = ollama._extra_body()
    assert body == {"num_ctx": 8192, "think": False}, f"Ollama 应带 num_ctx 与 think=False，实际 {body}"
    other = LLMClient(LLMConfig(base_url="http://127.0.0.1:9999/v1"))
    assert other._extra_body() == {}, "非 Ollama 端点不应带 extra_body"
    print("✓ qwen3 think 关闭与上下文窗口参数正确")


if __name__ == "__main__":
    test_build_workflow()
    test_selfie_missing_checkpoint()
    test_selfie_success_path()
    test_ref_image_missing()
    test_scenes_transcribe_flow()
    test_scenes_missing_audio()
    test_llm_extra_body()
    print("\n新功能单测全部通过 🎉")
