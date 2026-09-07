from typing import Any

from loguru import logger

from src.config.settings import settings
from src.core.retrieval.embedder import Qwen3Embedder
from src.core.retrieval.reranker import Qwen3Reranker
from src.core.retrieval.rrf_fusion import rrf_fusion
from src.core.retrieval.time_decay import apply_time_decay
from src.db.vector_store import VectorStore, get_vector_store


class HybridSearch:
    def __init__(
        self,
        embedder: Qwen3Embedder | None = None,
        vector_store: VectorStore | None = None,
        reranker: Qwen3Reranker | None = None,
    ) -> None:
        self.embedder = embedder or Qwen3Embedder()
        self.vector_store = vector_store or get_vector_store()
        # Local embedding workflows use the deterministic local reranker so
        # offline E2E tests never make an accidental network request.
        if reranker is None:
            provider = "local" if getattr(self.embedder, "provider", None) == "local" else None
            reranker = Qwen3Reranker(provider=provider)
        self.reranker = reranker

    @staticmethod
    def _literal(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def search(
        self,
        query: str,
        project_ids: list[str],
        top_k: int = 5,
        include_outdated: bool = False,
        content_types: list[str] | None = None,
        time_decay_enabled: bool = False,
    ) -> list[dict[str, Any]]:
        query_embedding = self.embedder.embed([query])[0]
        if not query_embedding:
            raise RuntimeError("embedding service returned an empty query vector")

        filters = []
        if not include_outdated:
            filters.append("version_status == 'active'")
        if project_ids:
            project_filters = [
                f"ARRAY_CONTAINS(project_ids, '{self._literal(project_id)}')"
                for project_id in project_ids
            ]
            filters.append("(" + " || ".join(project_filters) + ")")
        if content_types:
            content_filter = " || ".join(
                f"content_type == '{self._literal(content_type)}'" for content_type in content_types
            )
            filters.append(f"({content_filter})")

        filter_expression = " && ".join(filters) if filters else None
        dense_results = self.vector_store.search(
            vector=query_embedding,
            top_k=settings.dense_top_k,
            filter=filter_expression,
        )
        for rank, item in enumerate(dense_results, start=1):
            item["dense_rank"] = rank
            item["dense_score"] = item.get("score", 0.0)

        degraded: list[str] = []
        try:
            sparse_results = self.vector_store.keyword_search(
                query=query,
                top_k=settings.sparse_top_k,
                filter=filter_expression,
            )
        except (AttributeError, NotImplementedError) as exc:
            logger.warning(f"BM25 通道不可用，回退到 Dense: {exc}")
            sparse_results = []
            degraded.append("bm25")
        except Exception as exc:
            logger.warning(f"BM25 检索失败，回退到 Dense: {exc}")
            sparse_results = []
            degraded.append("bm25")
        for rank, item in enumerate(sparse_results, start=1):
            item["bm25_rank"] = rank
            item["bm25_score"] = item.get("score", 0.0)

        results = rrf_fusion([dense_results, sparse_results])
        if time_decay_enabled:
            results = apply_time_decay(results)

        candidates = results[: max(top_k, settings.rerank_top_n)]
        reranked = self._rerank(query, candidates, top_k=top_k, degraded=degraded)
        final_results = reranked[:top_k]
        if degraded:
            for item in final_results:
                item["retrieval_degraded"] = list(degraded)
        logger.info(
            "混合检索完成: dense={} bm25={} fused={} final={} degraded={}",
            len(dense_results),
            len(sparse_results),
            len(results),
            len(final_results),
            degraded,
        )
        return final_results

    def _rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        top_k: int,
        degraded: list[str],
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        try:
            ranked = self.reranker.rerank(
                query,
                [str(item.get("content", "")) for item in candidates],
                top_k=min(top_k, len(candidates)),
            )
        except Exception as exc:
            logger.warning(f"Reranker 检索失败，回退到 RRF 顺序: {exc}")
            degraded.append("reranker")
            return candidates

        output: list[dict[str, Any]] = []
        seen: set[int] = set()
        for rank, item in enumerate(ranked, start=1):
            index = item.get("index")
            if not isinstance(index, int) or not 0 <= index < len(candidates) or index in seen:
                degraded.append("reranker")
                continue
            seen.add(index)
            result = dict(candidates[index])
            result["rerank_score"] = float(item.get("score", 0.0))
            result["score"] = result["rerank_score"]
            result["rerank_rank"] = rank
            output.append(result)
        if not output:
            degraded.append("reranker")
            return candidates
        return output
