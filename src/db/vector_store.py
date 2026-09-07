import math
import re
from threading import Lock
from typing import Any, Protocol

from src.config.settings import settings
from src.core.retrieval.bm25 import BM25Retriever


class VectorStore(Protocol):
    def insert(self, entities: list[dict[str, Any]]) -> dict[str, int]: ...

    def delete(self, ids: list[str]) -> None: ...

    def search(
        self,
        vector: list[float],
        top_k: int = 10,
        filter: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def keyword_search(
        self,
        query: str,
        top_k: int = 10,
        filter: str | None = None,
    ) -> list[dict[str, Any]]: ...


class InMemoryVectorStore:
    """Process-local vector index for development and deterministic E2E tests."""

    def __init__(self) -> None:
        self._entities: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def clear(self) -> None:
        with self._lock:
            self._entities.clear()

    def insert(self, entities: list[dict[str, Any]]) -> dict[str, int]:
        with self._lock:
            for entity in entities:
                self._entities[entity["id"]] = dict(entity)
        return {"insert_count": len(entities)}

    def delete(self, ids: list[str]) -> None:
        with self._lock:
            for chunk_id in ids:
                self._entities.pop(chunk_id, None)

    @staticmethod
    def _matches(entity: dict[str, Any], expression: str | None) -> bool:
        if not expression:
            return True
        if "version_status == 'active'" in expression and entity.get("version_status") != "active":
            return False
        project_ids = re.findall(r"ARRAY_CONTAINS\(project_ids, '([^']+)'\)", expression)
        if project_ids and not set(project_ids).intersection(entity.get("project_ids", [])):
            return False
        content_types = re.findall(r"content_type == '([^']+)'", expression)
        return not content_types or entity.get("content_type") in content_types

    def search(
        self,
        vector: list[float],
        top_k: int = 10,
        filter: str | None = None,
    ) -> list[dict[str, Any]]:
        if not vector:
            raise ValueError("query vector must not be empty")
        results = []
        with self._lock:
            entities = list(self._entities.values())
        for entity in entities:
            if not self._matches(entity, filter):
                continue
            candidate = entity["dense_vector"]
            if len(candidate) != len(vector):
                raise ValueError("query and stored vector dimensions do not match")
            distance = math.sqrt(sum((left - right) ** 2 for left, right in zip(vector, candidate)))
            result = {key: value for key, value in entity.items() if key != "dense_vector"}
            result.update(distance=distance, score=1.0 / (1.0 + distance))
            results.append(result)
        return sorted(results, key=lambda item: item["distance"])[:top_k]

    def keyword_search(
        self,
        query: str,
        top_k: int = 10,
        filter: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            entities = list(self._entities.values())
        candidates = [entity for entity in entities if self._matches(entity, filter)]
        return BM25Retriever().search(query, candidates, top_k=top_k)


memory_vector_store = InMemoryVectorStore()


def get_vector_store() -> VectorStore:
    if settings.vector_store_backend == "memory":
        return memory_vector_store
    if settings.vector_store_backend != "milvus":
        raise ValueError(f"unsupported vector store backend: {settings.vector_store_backend}")
    from src.db.milvus_client import milvus_client

    return milvus_client
