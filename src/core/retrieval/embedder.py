import hashlib
import math
import re

import requests

from src.config.settings import settings
from src.utils.retry import retry_sync


class Qwen3Embedder:
    """OpenAI-compatible embedding client with an explicit local development mode."""

    def __init__(
        self,
        provider: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        dimension: int | None = None,
    ) -> None:
        self.provider = provider or settings.embedding_provider
        self.base_url = (base_url or settings.embedding_base_url).rstrip("/")
        self.api_key = settings.embedding_api_key if api_key is None else api_key
        self.model = model or settings.embedding_model
        self.dimension = dimension or settings.embedding_dimension

    def _local_embedding(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            vector[index] += -1.0 if digest[4] & 1 else 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.provider == "local":
            return [self._local_embedding(text) for text in texts]
        if self.provider != "remote":
            raise ValueError(f"unsupported embedding provider: {self.provider}")

        url = f"{self.base_url}/{settings.embedding_endpoint.lstrip('/')}"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = retry_sync(
            lambda: requests.post(
                url,
                json={"input": texts, "model": self.model},
                headers=headers,
                timeout=settings.embedding_timeout_seconds,
            ),
            attempts=settings.request_retry_attempts,
            backoff_seconds=settings.request_retry_backoff_seconds,
        )
        response.raise_for_status()
        data = response.json().get("data", [])
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        embeddings = [item.get("embedding", []) for item in ordered]
        if len(embeddings) != len(texts) or any(len(item) != self.dimension for item in embeddings):
            raise RuntimeError(
                f"embedding response shape mismatch: expected {len(texts)}x{self.dimension}",
            )
        return embeddings
