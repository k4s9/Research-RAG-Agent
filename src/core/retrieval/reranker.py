import re

import requests

from src.config.settings import settings
from src.utils.retry import retry_sync


class Qwen3Reranker:
    """Configurable rerank client. Local mode is only for offline workflow checks."""

    def __init__(self, provider: str | None = None) -> None:
        self.provider = provider or settings.reranker_provider

    @staticmethod
    def _local_score(query: str, document: str) -> float:
        query_terms = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", query.lower()))
        document_terms = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", document.lower()))
        return len(query_terms & document_terms) / max(len(query_terms), 1)

    def rerank(self, query: str, documents: list[str], top_k: int = 5) -> list[dict]:
        if not documents:
            return []
        if self.provider == "local":
            results = [
                {"index": index, "document": document, "score": self._local_score(query, document)}
                for index, document in enumerate(documents)
            ]
            return sorted(results, key=lambda item: item["score"], reverse=True)[:top_k]
        if self.provider != "remote":
            raise ValueError(f"unsupported reranker provider: {self.provider}")

        url = f"{settings.reranker_base_url.rstrip('/')}/{settings.reranker_endpoint.lstrip('/')}"
        headers = {"Content-Type": "application/json"}
        if settings.reranker_api_key:
            headers["Authorization"] = f"Bearer {settings.reranker_api_key}"
        response = retry_sync(
            lambda: requests.post(
                url,
                json={
                    "model": settings.reranker_model,
                    "query": query,
                    "documents": documents,
                    "top_k": top_k,
                },
                headers=headers,
                timeout=settings.reranker_timeout_seconds,
            ),
            attempts=settings.request_retry_attempts,
            backoff_seconds=settings.request_retry_backoff_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results", payload.get("data", []))
        if not isinstance(results, list):
            raise RuntimeError("reranker response must contain a results list")
        for item in results:
            index = item.get("index")
            if not isinstance(index, int) or not 0 <= index < len(documents):
                raise RuntimeError(f"reranker returned invalid document index: {index}")
        return results
