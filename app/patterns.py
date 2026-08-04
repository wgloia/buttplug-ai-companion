"""震动模式库：波形引擎。

[[pattern 名称]] / [[pattern 名称,强度]] / [[pattern 名称,强度,时长秒]]
波形以 10Hz 刷新率驱动设备马达，支持任意波形函数。
"""
from __future__ import annotations

import asyncio
import logging
import math
import time

log = logging.getLogger(__name__)

REFRESH_HZ = 10.0
STEP = 1.0 / REFRESH_HZ


def _heartbeat(t: float, base: float) -> float:
    """心跳：两段式脉冲（强-弱）。周期 1.2s。"""
    p = t % 1.2
    if p < 0.12:
        return base
    if p < 0.24:
        return base * 0.5
    if 0.55 <= p < 0.67:
        return base * 0.8
    if 0.67 <= p < 0.76:
        return base * 0.4
    return base * 0.15


def _wave(t: float, base: float) -> float:
    """波浪：正弦起伏。周期 3s，最低 30%。"""
    return base * (0.3 + 0.7 * (math.sin(2 * math.pi * t / 3.0) * 0.5 + 0.5))


def _pulse(t: float, base: float) -> float:
    """脉动：方波。0.5s 满 / 0.5s 停。"""
    return base if (t % 1.0) < 0.5 else 0.0


def _climb(t: float, base: float) -> float:
    """爬升：0 → base 线性爬升后回落。周期 5s。"""
    p = (t % 5.0) / 5.0
    return base * (p if p < 0.8 else (1.0 - (p - 0.8) * 5))


def _tremble(t: float, base: float) -> float:
    """颤抖：高频小幅抖动。4Hz。"""
    return base * (0.5 + 0.5 * (math.sin(2 * math.pi * t * 4.0) * 0.5 + 0.5))


def _cruise(t: float, base: float) -> float:
    """巡航：恒定强度。"""
    return base


PATTERNS: dict[str, callable] = {
    "心跳": _heartbeat,
    "heartbeat": _heartbeat,
    "波浪": _wave,
    "wave": _wave,
    "脉动": _pulse,
    "pulse": _pulse,
    "爬升": _climb,
    "climb": _climb,
    "颤抖": _tremble,
    "tremble": _tremble,
    "巡航": _cruise,
    "cruise": _cruise,
}


class PatternEngine:
    """模式执行器：同一时刻只运行一个模式，新模式/停止命令替换旧模式。"""

    def __init__(self, toy, safety):
        self.toy = toy
        self.safety = safety
        self._task: asyncio.Task | None = None
        self._cancel = asyncio.Event()
        self.active_name = ""

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, name: str, base_intensity_100: int, duration_sec: int = 0) -> bool:
        """启动模式。name 未知返回 False。强度自动钳制到安全上限。"""
        fn = PATTERNS.get(name)
        if fn is None:
            log.warning("未知模式: %s（可用: %s）", name, list(PATTERNS))
            return False
        await self.stop()
        self._cancel.clear()
        base = min(100, max(0, base_intensity_100)) / 100.0
        base = min(base, self.safety.config.max_intensity / 100.0)
        self.active_name = name
        self._task = asyncio.create_task(self._run(fn, name, base, duration_sec))
        log.info("启动模式「%s」强度 %d%% 时长 %ss", name, int(base * 100), duration_sec or "∞")
        return True

    async def stop(self) -> None:
        if self._task is not None:
            self._cancel.set()
            try:
                await asyncio.wait_for(self._task, timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
            self.active_name = ""
            await self.toy.stop_all()

    async def _run(self, fn, name: str, base: float, duration_sec: int) -> None:
        t0 = time.monotonic()
        try:
            while not self._cancel.is_set():
                elapsed = time.monotonic() - t0
                if duration_sec and elapsed >= duration_sec:
                    break
                intensity = max(0.0, min(1.0, fn(elapsed, base)))
                await self.toy.vibrate(0, intensity)
                # 让看门狗知道模式仍在活跃
                self.safety.remember_active(0)
                await asyncio.sleep(STEP)
        except asyncio.CancelledError:
            pass
        finally:
            if self._task is not None and not self._cancel.is_set():
                # 自然结束（时长到），停下来
                await self.toy.stop_all()
            self.active_name = ""
