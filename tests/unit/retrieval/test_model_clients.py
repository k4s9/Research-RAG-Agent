import pytest

from src.config.settings import settings
from src.core.retrieval.embedder import Qwen3Embedder
from src.core.retrieval.reranker import Qwen3Reranker

pytestmark = pytest.mark.unit


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def test_remote_embedding_uses_openai_compatible_contract(monkeypatch) -> None:
    captured = {}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return FakeResponse(
            {
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    monkeypatch.setattr("src.core.retrieval.embedder.requests.post", post)
    monkeypatch.setattr(settings, "embedding_endpoint", "/v1/embeddings")
    client = Qwen3Embedder(
        provider="remote",
        base_url="https://models.example",
        api_key="secret",
        dimension=2,
    )

    assert client.embed(["first", "second"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert captured["url"] == "https://models.example/v1/embeddings"
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_remote_reranker_rejects_invalid_index(monkeypatch) -> None:
    monkeypatch.setattr(settings, "reranker_base_url", "https://models.example")
    monkeypatch.setattr(settings, "reranker_endpoint", "/rerank")
    monkeypatch.setattr(
        "src.core.retrieval.reranker.requests.post",
        lambda *args, **kwargs: FakeResponse({"results": [{"index": 3, "score": 0.9}]}),
    )

    with pytest.raises(RuntimeError, match="invalid document index"):
        Qwen3Reranker(provider="remote").rerank("query", ["only document"])


def test_local_model_adapters_are_deterministic() -> None:
    embedder = Qwen3Embedder(provider="local", dimension=16)
    assert embedder.embed(["same text"])[0] == embedder.embed(["same text"])[0]
    results = Qwen3Reranker(provider="local").rerank(
        "verified evidence",
        ["unrelated", "verified evidence"],
        top_k=1,
    )
    assert results[0]["index"] == 1


def test_remote_reranker_top_n_and_relevance_score(monkeypatch):
    captured = {}
    result = [{"index": 1, "relevance_score": 0.97}]

    def post(url, **kwargs):
        captured.update(kwargs["json"])
        return FakeResponse({"results": result})

    monkeypatch.setattr("src.core.retrieval.reranker.requests.post", post)
    assert Qwen3Reranker(provider="remote").rerank("q", ["a", "b"], 1) == result
    assert captured["top_n"] == 1
    assert "top_k" not in captured


@pytest.mark.parametrize("rows", [
    [{"index": 0}],
    [{"index": 0, "score": float("nan")}],
    [{"index": 0, "score": 0.8}, {"index": 0, "score": 0.7}],
    [],
])
def test_remote_reranker_rejects_unusable_results(monkeypatch, rows):
    monkeypatch.setattr(
        "src.core.retrieval.reranker.requests.post",
        lambda *a, **k: FakeResponse({"results": rows}),
    )
    with pytest.raises(RuntimeError):
        Qwen3Reranker(provider="remote").rerank("q", ["a", "b"], 1)
