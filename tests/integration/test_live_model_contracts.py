import os

import pytest

from src.core.retrieval.embedder import Qwen3Embedder
from src.core.retrieval.reranker import Qwen3Reranker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_INTEGRATION") != "1",
        reason="set RUN_LIVE_INTEGRATION=1 after configuring model APIs",
    ),
]


def test_live_embedding_and_reranker_contracts() -> None:
    embeddings = Qwen3Embedder().embed(["contract probe", "second probe"])
    assert len(embeddings) == 2
    assert embeddings[0]
    assert len(embeddings[0]) == len(embeddings[1])

    results = Qwen3Reranker().rerank(
        "verified contract",
        ["unrelated text", "verified contract evidence"],
        top_k=2,
    )
    assert results
    assert {result["index"] for result in results}.issubset({0, 1})
