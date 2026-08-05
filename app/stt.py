"""STT 共享模型加载器：语音输入（/api/stt）与剧情转写（scenes）共用同一个模型实例。

此前两处各自加载 faster-whisper 模型（浪费约 1-2GB 内存），且首次加载会阻塞
事件循环——这里统一为进程级单例，加载动作由调用方放进线程池执行。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_model = None


def get_stt_model(model_name: str = "small") -> object:
    """懒加载 faster-whisper 模型（进程级单例，线程安全由调用方保证）。

    注意：首次加载约 1 分钟，调用方必须在线程池 executor 中调用，
    否则会冻结 asyncio 事件循环（紧急停止等请求全部排队）。
    """
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        log.info("加载 STT 模型 (faster-whisper %s, cpu/int8)，首次约 1 分钟…", model_name)
        _model = WhisperModel(model_name, device="cpu", compute_type="int8")
        log.info("STT 模型加载完成")
    return _model
