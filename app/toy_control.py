"""Intiface Central 玩具控制客户端（裸 WebSocket，buttplug v3 协议）。

不依赖 buttplug-py，直接与 Intiface Central 的 WebSocket 端口通信。
未连接或连接失败时自动降级为「日志模式」：命令只记录不发送，方便无硬件调试。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import contextmanager
from typing import Callable

import websockets


@contextmanager
def _bypass_proxy():
    """临时清除代理环境变量，避免 websockets 走系统代理导致 502。"""
    proxy_vars = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]
    saved = {k: os.environ.pop(k, None) for k in proxy_vars}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

log = logging.getLogger(__name__)

DeviceEventCallback = Callable[[list[dict]], None]


class ToyController:
    def __init__(self, ws_url: str = "ws://127.0.0.1:12345"):
        self.ws_url = ws_url
        self.ws = None
        self._next_id = 1
        self.devices: list[dict] = []
        self.log_mode = False
        self.on_devices_changed: DeviceEventCallback | None = None

    # ---- 连接 ----

    async def connect(self) -> bool:
        try:
            with _bypass_proxy():
                self.ws = await websockets.connect(self.ws_url, open_timeout=5)
            # 握手
            await self._send_raw({"RequestServerInfo": {
                "Id": self._next_id(), "ClientName": "buttplug-ai-companion", "MessageVersion": 3}})
            msg = await self._recv_raw()
            if "ServerInfo" in msg:
                info = msg["ServerInfo"]
                log.info("已连接 Intiface: %s (协议 v%s)", info.get("ServerName"), info.get("MessageVersion"))
                self.log_mode = False
                asyncio.create_task(self._event_loop())
                return True
            log.warning("Intiface 握手失败: %s", msg)
        except Exception as exc:
            log.warning("无法连接 Intiface (%s)，进入日志模式（命令仅记录不发送）", exc)
        self.ws = None
        self.log_mode = True
        return False

    async def _event_loop(self) -> None:
        """持续接收服务器消息：响应与设备事件。"""
        try:
            while True:
                msg = await self._recv_raw()
                self._handle_message(msg)
        except Exception:
            log.warning("Intiface 连接断开，降级为日志模式")
            self.ws = None
            self.log_mode = True

    def _handle_message(self, msg: dict) -> None:
        if "DeviceAdded" in msg:
            dev = msg["DeviceAdded"]
            self.devices.append(dev)
            log.info("设备接入: %s", dev.get("DeviceName"))
            self._notify()
        elif "DeviceRemoved" in msg:
            idx = msg["DeviceRemoved"].get("DeviceIndex")
            self.devices = [d for d in self.devices if d.get("DeviceIndex") != idx]
            log.info("设备移除: index %s", idx)
            self._notify()
        elif "Error" in msg:
            log.warning("Intiface 返回错误: %s", msg["Error"])

    def _notify(self) -> None:
        if self.on_devices_changed:
            try:
                self.on_devices_changed(list(self.devices))
            except Exception:
                log.exception("设备变更回调异常")

    # ---- 收发 ----

    def _next_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _send_raw(self, payload: dict) -> None:
        if self.ws is None:
            return
        await self.ws.send(json.dumps(payload))

    async def _recv_raw(self) -> dict:
        if self.ws is None:
            return {}
        raw = await asyncio.wait_for(self.ws.recv(), timeout=30)
        return json.loads(raw)

    async def _command(self, payload: dict) -> dict | None:
        if self.log_mode:
            return None
        try:
            await self._send_raw(payload)
            return await self._recv_raw()
        except Exception as exc:
            log.warning("发送命令失败: %s", exc)
            return None

    # ---- 业务命令 ----

    async def scan(self) -> None:
        await self._command({"StartScanning": {"Id": self._next_id()}})

    async def stop_scan(self) -> None:
        await self._command({"StopScanning": {"Id": self._next_id()}})

    async def request_device_list(self) -> list[dict]:
        resp = await self._command({"RequestDeviceList": {"Id": self._next_id()}})
        if resp and "DeviceList" in resp:
            self.devices = resp["DeviceList"].get("Devices", [])
            self._notify()
        return list(self.devices)

    async def vibrate(self, device_index: int, speed_01: float, motor: int = 0) -> bool:
        """设置指定设备某马达的振动强度 (0.0-1.0)。返回是否实际发送。"""
        if self.log_mode:
            log.info("[日志模式] 设备%s 马达%s 振动 %s%%", device_index, motor, int(speed_01 * 100))
            return False
        resp = await self._command({"SingleVibrateCmd": {
            "Id": self._next_id(),
            "DeviceIndex": device_index,
            "Speed": round(max(0.0, min(1.0, speed_01)), 3),
        }})
        return bool(resp and "Ok" in resp)

    async def stop_device(self, device_index: int) -> None:
        if self.log_mode:
            log.info("[日志模式] 停止设备%s", device_index)
            return
        await self._command({"StopDeviceCmd": {"Id": self._next_id(), "DeviceIndex": device_index}})

    async def stop_all(self) -> None:
        if self.log_mode:
            log.info("[日志模式] 停止全部设备")
            return
        await self._command({"StopAllDevices": {"Id": self._next_id()}})
