"""Run a tiny deterministic retrieval regression over a JSONL dataset."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path

from src.core.retrieval.embedder import Qwen3Embedder
from src.core.retrieval.hybrid_search import HybridSearch
from src.core.retrieval.reranker import Qwen3Reranker
from src.db.vector_store import InMemoryVectorStore


def _validate_sample(sample: dict, index: int) -> None:
    if not isinstance(sample.get("query"), str) or not sample["query"].strip():
        raise ValueError(f"sample {index} query must be a non-empty string")
    if not isinstance(sample.get("gold_ids"), list) or not sample["gold_ids"]:
        raise ValueError(f"sample {index} has no gold_ids")
    documents = sample.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError(f"sample {index} has no documents")
    document_ids = [document.get("id") for document in documents]
    if any(not isinstance(document_id, str) for document_id in document_ids):
        raise ValueError(f"sample {index} documents require string ids")
    if len(set(document_ids)) != len(document_ids):
        raise ValueError(f"sample {index} contains duplicate document ids")


def evaluate(dataset_path: Path) -> dict[str, object]:
    samples = [
        json.loads(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not samples:
        raise ValueError("evaluation dataset is empty")
    latencies: list[float] = []
    hits = {1: 0, 3: 0, 5: 0}
    embedder = Qwen3Embedder(provider="local", dimension=64)
    for index, sample in enumerate(samples, start=1):
        _validate_sample(sample, index)
        gold = set(sample.get("gold_ids", []))
        store = InMemoryVectorStore()
        documents = []
        for document in sample["documents"]:
            documents.append(
                {
                    **document,
                    "entity_type": "chunk",
                    "dense_vector": embedder.embed([document["content"]])[0],
                    "project_ids": ["eval"],
                    "version_status": "active",
                    "content_type": "text",
                    "created_at": 0,
                },
            )
        store.insert(documents)
        searcher = HybridSearch(
            embedder=embedder,
            vector_store=store,
            reranker=Qwen3Reranker(provider="local"),
        )
        start = time.perf_counter()
        results = searcher.search(
            sample["query"],
            project_ids=["eval"],
            top_k=max(hits),
        )
        latencies.append((time.perf_counter() - start) * 1000)
        ids = [item["id"] for item in results]
        for k in hits:
            hits[k] += int(bool(gold.intersection(ids[:k])))
    count = len(samples)
    sorted_latencies = sorted(latencies)
    p95_index = min(count - 1, max(0, math.ceil(count * 0.95) - 1))
    return {
        "samples": count,
        "recall_at_1": hits[1] / count,
        "recall_at_3": hits[3] / count,
        "recall_at_5": hits[5] / count,
        "latency_ms_mean": statistics.mean(latencies),
        "latency_ms_p95": sorted_latencies[p95_index],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.dataset), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
