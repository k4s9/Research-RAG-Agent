"""Nonblocking chat-completion client for background document enrichment."""

import asyncio
from typing import Any

import httpx

from src.config.settings import settings
from src.utils.llm_client import LLMClient


class AsyncLLMClient:
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self.http_client = http_client

    async def generate(self, prompt: str) -> str:
        if settings.llm_provider == "local":
            return LLMClient._call_local(prompt)
        if settings.llm_provider not in {"deepseek", "openai"}:
            raise ValueError(f"不支持的 LLM 提供商: {settings.llm_provider}")
        if self.http_client is not None:
            return await self._request(self.http_client, prompt)
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            return await self._request(client, prompt)

    async def _request(self, client: httpx.AsyncClient, prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": settings.llm_model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 1024,
            **LLMClient.inference_options(),
        }
        attempts = max(1, settings.request_retry_attempts)
        for attempt in range(attempts):
            try:
                response = await client.post(
                    f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                    json=payload,
                    timeout=settings.llm_timeout_seconds,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                if not isinstance(content, str):
                    raise ValueError("LLM response content must be text")
                return content
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                    if status != 429 and status < 500:
                        raise
                if attempt + 1 == attempts:
                    raise
                await asyncio.sleep(settings.request_retry_backoff_seconds * (2**attempt))
        raise RuntimeError("LLM request did not return")
