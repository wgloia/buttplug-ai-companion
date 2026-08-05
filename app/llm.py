"""OpenAI 兼容大模型客户端（支持 Ollama / LM Studio / 任意云端 API）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
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
        # trust_env=False：忽略系统代理（本地模型服务被代理劫持会 502）
        self.client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key,
                                  http_client=httpx.AsyncClient(trust_env=False))

    def _extra_body(self) -> dict:
        """Ollama 需要显式 num_ctx 才能用满配置的上下文长度；qwen3 关闭思考模式（角色扮演实时性）。"""
        if "11434" in self.config.base_url:
            return {"num_ctx": self.config.context_length, "think": False}
        return {}

    async def stream_chat(self, messages: list[dict]):
        """流式生成。yield 文本增量。若底层不支持流式则回退为一次性返回。"""
        try:
            stream = await self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                stream=True,
                extra_body=self._extra_body(),
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
                extra_body=self._extra_body(),
            )
            content = resp.choices[0].message.content or ""
            # 按词切分，保持流式接口一致
            for i in range(0, len(content), 4):
                yield content[i:i + 4]
