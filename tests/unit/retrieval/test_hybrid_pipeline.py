import pytest

from src.core.retrieval.bm25 import BM25Retriever
from src.core.retrieval.hybrid_search import HybridSearch
from src.core.retrieval.rrf_fusion import rrf_fusion


pytestmark = pytest.mark.unit


def test_bm25_prefers_exact_keyword_match() -> None:
    retriever = BM25Retriever(
        [
            {"id": "broad", "content": "calibration protocol and timing"},
            {"id": "exact", "content": "verified calibration calibration"},
        ]
    )

    results = retriever.search("verified calibration", top_k=2)

    assert results[0]["id"] == "exact"
    assert results[0]["bm25_score"] > results[1]["bm25_score"]


def test_rrf_deduplicates_and_keeps_channel_ranks() -> None:
    results = rrf_fusion(
        [
            [{"id": "same", "dense_score": 0.8}, {"id": "dense-only"}],
            [{"id": "same", "bm25_score": 3.0}, {"id": "bm25-only"}],
        ],
        k=60,
    )

    same = next(item for item in results if item["id"] == "same")
    assert same["dense_rank"] == 1
    assert same["bm25_rank"] == 1
    assert same["retrieval_channels"] == ["bm25", "dense"]
    assert same["rrf_score"] == pytest.approx(2 / 61)


class FakeEmbedder:
    provider = "local"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class FakeStore:
    def search(self, **kwargs):
        return [
            {"id": "dense", "content": "semantic evidence", "score": 0.7},
            {"id": "shared", "content": "exact evidence", "score": 0.6},
        ]

    def keyword_search(self, **kwargs):
        return [
            {"id": "shared", "content": "exact evidence", "score": 2.0},
            {"id": "sparse", "content": "exact keyword", "score": 1.0},
        ]


class FakeReranker:
    def rerank(self, query: str, documents: list[str], top_k: int = 5):
        del query, documents
        return [{"index": 1, "score": 0.99}, {"index": 0, "score": 0.5}][:top_k]


def test_hybrid_search_runs_bm25_rrf_and_reranker() -> None:
    searcher = HybridSearch(
        embedder=FakeEmbedder(),
        vector_store=FakeStore(),
        reranker=FakeReranker(),
    )

    results = searcher.search("exact evidence", project_ids=["project-1"], top_k=2)

    assert [item["id"] for item in results] == ["dense", "shared"]
    assert results[1]["bm25_rank"] == 1
    assert results[1]["dense_rank"] == 2
    assert results[0]["rerank_score"] == 0.99
