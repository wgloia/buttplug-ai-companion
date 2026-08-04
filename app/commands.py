"""[[命令]] 解析器与安全钳制层。

LLM 在回复文本中内嵌形如 [[vibrate 60]] 的指令标记，
本模块负责提取、钳制（强度/时长上限）并交给玩具控制层执行。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# [[vibrate 60]] / [[vibrate 0,80]] / [[vibrate 0 80]] / [[stop]] / [[pattern name]]
CMD_RE = re.compile(r"\[\[\s*(\w+)(.*?)\s*\]\]", re.S)


@dataclass
class SafetyConfig:
    max_intensity: int = 80          # 0-100
    watchdog_seconds: int = 15       # 无时长命令的自动停止秒数
    safeword: str = "red"


@dataclass
class DeviceCommand:
    kind: str                        # vibrate / stop / pattern / unknown
    index: int = 0                   # 马达/设备标号
    intensity: int = 0               # 0-100
    name: str = ""                   # pattern 名称
    duration: int = 0                # pattern 时长（秒），0=持续（看门狗管理）
    raw: str = ""
    timestamp: float = field(default_factory=time.time)


class CommandSafety:
    """解析 + 钳制 + 看门狗。不直接接触硬件，便于测试。"""

    def __init__(self, config: SafetyConfig):
        self.config = config
        self._active: dict[int, float] = {}   # 设备索引 -> 最后激活时间戳
        self._watchdog_task: asyncio.Task | None = None

    # ---- 解析 ----

    def parse_stream(self, text: str) -> list[DeviceCommand]:
        """从（可能是不完整的）文本中提取命令。重复文本中已见过的命令不会重复返回。"""
        cmds: list[DeviceCommand] = []
        for match in CMD_RE.finditer(text):
            kind = match.group(1).lower()
            raw = match.group(0)
            args = [a for a in re.split(r"[\s,，]+", match.group(2).strip()) if a]
            try:
                if kind == "stop":
                    cmds.append(DeviceCommand(kind="stop", raw=raw))
                elif kind == "vibrate" and args:
                    if len(args) == 1:
                        idx, strength = 0, int(args[0])
                    else:
                        idx, strength = int(args[0]), int(args[1])
                    cmds.append(self._clamp(DeviceCommand(
                        kind="vibrate", index=idx, intensity=strength, raw=raw)))
                elif kind == "pattern":
                    if args:
                        base = int(args[1]) if len(args) > 1 and args[1].isdigit() else 100
                        dur = int(args[2]) if len(args) > 2 and args[2].isdigit() else 0
                        cmds.append(self._clamp(DeviceCommand(
                            kind="pattern", name=args[0], index=base, duration=dur, raw=raw)))
                    else:
                        log.warning("pattern 命令缺少名称: %s", raw)
            except (ValueError, IndexError):
                log.warning("忽略无法解析的命令: %s", raw)
        return cmds

    def _clamp(self, cmd: DeviceCommand) -> DeviceCommand:
        if cmd.kind == "pattern":
            cmd.index = min(max(0, cmd.index), self.config.max_intensity)
        else:
            cmd.intensity = min(max(0, cmd.intensity), self.config.max_intensity)
        return cmd

    # ---- 状态 ----

    def remember_active(self, cmd: DeviceCommand) -> None:
        """记录最近一次指令，供看门狗判定超时。"""
        self._active[cmd.index] = time.time()

    def clear_active(self) -> None:
        self._active.clear()

    def is_stale(self, index: int, now: float | None = None) -> bool:
        now = now or time.time()
        ts = self._active.get(index)
        return ts is not None and (now - ts) > self.config.watchdog_seconds

    # ---- 看门狗 ----

    async def run_watchdog(self, stop_callback):
        """循环检查：若有设备超过 watchdog_seconds 未收到新指令，则调用 stop_callback。"""
        while True:
            await asyncio.sleep(1)
            now = time.time()
            for index in list(self._active):
                if self.is_stale(index, now):
                    log.warning("看门狗：设备 %s 超过 %ss 无新指令，自动停止", index, self.config.watchdog_seconds)
                    self._active.pop(index, None)
                    try:
                        await stop_callback(index)
                    except Exception:
                        log.exception("看门狗停止设备失败")

    def start_watchdog(self, stop_callback) -> asyncio.Task:
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self.run_watchdog(stop_callback))
        return self._watchdog_task
