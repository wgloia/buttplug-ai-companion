"""OpenAI 兼容大模型客户端（支持 Ollama / LM Studio / 任意云端 API）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

log = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    base_url: str = "http://127.0.0.1:11434/v1"
    api_key: str = "ollama"
    model: str = "qwen2.5:7b-instruct-q4_K_M"
    temperature: float = 0.9
    max_tokens: int = 1024
    context_length: int = 8192


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)

    async def stream_chat(self, messages: list[dict]):
        """流式生成。yield 文本增量。若底层不支持流式则回退为一次性返回。"""
        try:
            stream = await self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as exc:
            log.warning("流式调用失败，尝试非流式回退: %s", exc)
            resp = await self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                stream=False,
            )
            content = resp.choices[0].message.content or ""
            # 按词切分，保持流式接口一致
            for i in range(0, len(content), 4):
                yield content[i:i + 4]
