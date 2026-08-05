"""女友子模块：微信桥（小号 + hook 方案）。

监听微信个人号消息 → 调用 buttplug-ai-companion 的 /api/chat（本地 qwen3:8b-abliterated
大脑）→ 回复文本；若回复流中出现 selfie 命令（生成的自拍图），把图片发送给微信。

依赖（需自行安装并登录）:
    pip install ntchat          # PC 微信 hook（小号，有封号风险，仅用于测试账号）
    Windows + 微信 3.9.x 特定版本（见 ntchat 项目说明）

用法:
    python -m girlfriend.wechat_bridge            # 使用默认配置
    WECHAT_NSFW=1 python -m girlfriend.wechat_bridge

注意:
    - 必须使用小号！hook 个人微信违反腾讯用户协议，存在封号风险
    - 启动前需保持微信 PC 客户端登录小号
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.request

log = logging.getLogger("wechat_bridge")

API_BASE = os.environ.get("GIRLFRIEND_API", "http://127.0.0.1:8000")
WECHAT_SESSION_PREFIX = "wx:"          # 会话隔离前缀（按微信号）
SELFIE_KEYWORDS = ("自拍", "发张照片", "发张图", "拍一张", "发我看看", "你在干嘛", "你在哪")

_http_pool = threading.local()


def _post(path: str, payload: dict, timeout: int = 300):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API_BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _stream_chat(wxid: str, message: str):
    """调用 /api/chat（SSE），返回 (文本回复, [图片URL列表])。"""
    import urllib.parse

    payload = {"session_id": WECHAT_SESSION_PREFIX + wxid, "message": message}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API_BASE + "/api/chat", data=data,
                                 headers={"Content-Type": "application/json"})
    text, images = [], []
    with urllib.request.urlopen(req, timeout=300) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                d = json.loads(line[5:].strip())
            except Exception:
                continue
            if isinstance(d, str):
                text.append(d)
            elif isinstance(d, dict) and d.get("kind") == "selfie" and d.get("url"):
                images.append(d["url"])
    return "".join(text), images


def _download(url: str) -> bytes:
    req = urllib.request.Request(API_BASE + url)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


class WeChatBridge:
    def __init__(self, ntchat_module):
        self.nt = ntchat_module
        self.client = None

    def on_message(self, client, context):
        data = context.get("data", {})
        msg_type = data.get("type", 0)
        wxid = data.get("from_wxid", "") or data.get("wxid", "")
        content = (data.get("msg") or data.get("raw_msg") or "").strip()
        if not wxid or not content:
            return
        # 只处理私聊文本（type 1 = 文本），忽略群消息与命令
        if msg_type != 1 or content.startswith("/"):
            return
        log.info("微信消息来自 %s: %s", wxid, content[:30])
        try:
            reply, images = _stream_chat(wxid, content)
        except Exception as exc:
            log.exception("对话调用失败")
            self.client.send_text(wxid, "（我这边出小问题了，稍等一下～）")
            return
        if reply:
            self.client.send_text(wxid, reply)
        for url in images:
            try:
                img = _download(url)
                tmp = f"selfie_{abs(hash(url)) % 10**8}.png"
                with open(tmp, "wb") as f:
                    f.write(img)
                self.client.send_image(wxid, tmp)
                os.remove(tmp)
            except Exception:
                log.exception("发送图片失败")

    def run(self):
        self.client = self.nt.attach()
        self.nt.add_listener(self.on_message)
        log.info("微信桥已启动，等待消息…（Ctrl+C 退出）")
        threading.Event().wait()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        import ntchat
    except ImportError:
        print("缺少 ntchat：pip install ntchat（需匹配的微信 PC 版本，小号登录，有封号风险）")
        return
    WeChatBridge(ntchat).run()


if __name__ == "__main__":
    main()
