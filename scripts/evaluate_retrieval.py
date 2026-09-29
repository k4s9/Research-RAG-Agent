"""Compare retrieval stages on one frozen corpus; stub scores are regression only."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.config.settings import settings
from src.core.retrieval.embedder import Qwen3Embedder
from src.core.retrieval.reranker import Qwen3Reranker
from src.core.retrieval.rrf_fusion import rrf_fusion
from src.db.vector_store import InMemoryVectorStore

METHODS = ("dense", "hybrid", "hybrid_rerank")
KS = (1, 3, 5)


@dataclass(frozen=True)
class EvaluationConfig:
    mode: str = "stub"
    dense_top_k: int = 50
    sparse_top_k: int = 50
    rerank_top_n: int = 20
    rrf_k: int = 60
    repeats: int = 1
    embedding_batch_size: int = 16
    stub_dimension: int = 64

    def __post_init__(self):
        if self.mode not in {"stub", "remote"}:
            raise ValueError("mode must be stub or remote")
        for field in ("dense_top_k", "sparse_top_k", "rerank_top_n"):
            if type(getattr(self, field)) is not int or getattr(self, field) < max(KS):
                raise ValueError(f"{field} must be an integer >= {max(KS)}")
        for field in ("repeats", "embedding_batch_size", "stub_dimension"):
            if type(getattr(self, field)) is not int or getattr(self, field) < 1:
                raise ValueError(f"{field} must be a positive integer")
        if type(self.rrf_k) is not int or self.rrf_k < 0:
            raise ValueError("rrf_k must be a nonnegative integer")


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{path.name} must contain nonempty JSONL objects")
    return rows


def load_dataset(dataset_path: Path, corpus_path: Path | None = None):
    """Inline corpora are merged once; conflicting IDs can never change per query."""
    samples = read_jsonl(dataset_path)
    corpus = {}
    if corpus_path:
        raw_documents = read_jsonl(corpus_path)
        if any("documents" in sample for sample in samples):
            raise ValueError("use either --corpus or inline documents, not both")
    else:
        raw_documents = []
        for sample in samples:
            documents = sample.get("documents")
            if not isinstance(documents, list) or not documents:
                raise ValueError("provide --corpus or nonempty inline documents for every sample")
            raw_documents.extend(documents)
    for document in raw_documents:
        if not isinstance(document, dict):
            raise ValueError("each corpus document must be an object")
        doc_id, content = document.get("id"), document.get("content")
        if not isinstance(doc_id, str) or not doc_id.strip():
            raise ValueError("documents require nonempty string ids")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"document {doc_id} requires nonempty content")
        if doc_id in corpus and corpus[doc_id] != document:
            raise ValueError(f"conflicting corpus records for {doc_id}")
        corpus[doc_id] = document
    seen = set()
    for index, sample in enumerate(samples, 1):
        sample.setdefault("id", f"sample-{index}")
        if not isinstance(sample["id"], str) or not sample["id"] or sample["id"] in seen:
            raise ValueError("samples require unique nonempty string ids")
        seen.add(sample["id"])
        if not isinstance(sample.get("query"), str) or not sample["query"].strip():
            raise ValueError(f"sample {index} query must be nonempty")
        gold = sample.get("gold_ids")
        if not isinstance(gold, list) or any(not isinstance(item, str) for item in gold):
            raise ValueError(f"sample {index} gold_ids must be a string list")
        if len(set(gold)) != len(gold) or not set(gold).issubset(corpus):
            raise ValueError(f"sample {index} has duplicate or unknown gold_ids")
        if not gold and sample.get("answerable") is not False:
            raise ValueError(
                "empty gold_ids require answerable=false; missing labels are not no-answer"
            )
        if gold and sample.get("answerable") is False:
            raise ValueError("unanswerable samples cannot have gold_ids")
        annotation = sample.get("annotation", {})
        if not isinstance(annotation, dict):
            raise ValueError("annotation must be an object")
        if annotation.get("status") == "human_reviewed" and not all(
            isinstance(annotation.get(key), str) and annotation[key].strip()
            for key in ("reviewer", "reviewed_at")
        ):
            raise ValueError("human_reviewed requires reviewer and reviewed_at; never infer review")
    return samples, [corpus[key] for key in sorted(corpus)]


def ranking_metrics(ids: list[str], gold: list[str]) -> dict:
    """Macro query recall differs from hit rate when a query has multiple golds."""
    if not gold:
        return {f"{metric}_at_{k}": None for metric in ("hit", "recall") for k in KS}
    relevant = set(gold)
    values = {}
    for k in KS:
        found = len(relevant.intersection(ids[:k]))
        values[f"hit_at_{k}"] = float(found > 0)
        values[f"recall_at_{k}"] = found / len(relevant)
    return values


def _model_identity(config, embedder, reranker):
    if config.mode == "stub":
        return {
            "embedding": {
                "provider": "local",
                "algorithm": "sha256_token_hash",
                "dimension": embedder.dimension,
            },
            "reranker": {"provider": "local", "algorithm": "query_token_overlap"},
            "deployment_identity_verified": False,
        }
    # Effective request configuration, never keys or credential-bearing URLs.
    return {
        "embedding": {
            "provider": embedder.provider,
            "requested_model": embedder.model,
            "dimension": embedder.dimension,
            "endpoint_sha256": digest([embedder.base_url, settings.embedding_endpoint]),
            "timeout_seconds": settings.embedding_timeout_seconds,
        },
        "reranker": {
            "provider": reranker.provider,
            "requested_model": settings.reranker_model,
            "endpoint_sha256": digest([settings.reranker_base_url, settings.reranker_endpoint]),
            "timeout_seconds": settings.reranker_timeout_seconds,
        },
        "retry_attempts": settings.request_retry_attempts,
        "retry_backoff_seconds": settings.request_retry_backoff_seconds,
        "deployment_identity_verified": False,
        "identity_note": "Requested model names are recorded; serving weights/templates are not independently verified.",
    }


def _source_hashes():
    root = Path(__file__).resolve().parents[1]
    paths = [
        Path(__file__).resolve(),
        root / "src/db/vector_store.py",
        root / "src/config/settings.py",
    ]
    paths += sorted((root / "src/core/retrieval").glob("*.py"))
    paths.append(root / "src/utils/retry.py")
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }


def _summarize(rows):
    summaries = {}
    for method in METHODS:
        selected = [row for row in rows if row["method"] == method]
        answerable = [row for row in selected if row["gold_ids"]]
        latencies = sorted(row["latency_ms"] for row in selected)
        summary = {
            "attempts": len(selected),
            "failed": sum(row["status"] != "completed" for row in selected),
            "answerable_attempts": len(answerable),
            "unanswerable_attempts": len(selected) - len(answerable),
            "latency_ms_mean": statistics.mean(latencies) if latencies else None,
            "latency_ms_p50": latencies[math.ceil(len(latencies) * 0.5) - 1] if latencies else None,
            "latency_ms_p95": latencies[math.ceil(len(latencies) * 0.95) - 1]
            if latencies
            else None,
        }
        for metric in ("hit", "recall"):
            for k in KS:
                name = f"{metric}_at_{k}"
                summary[name] = (
                    statistics.mean(row["metrics"][name] for row in answerable)
                    if answerable
                    else None
                )
        summaries[method] = summary
    return summaries


def evaluate(
    dataset_path: Path, *, corpus_path: Path | None = None, config: EvaluationConfig | None = None
) -> dict:
    config = config or EvaluationConfig()
    samples, corpus = load_dataset(dataset_path, corpus_path)
    provider = "local" if config.mode == "stub" else "remote"
    embedder = Qwen3Embedder(
        provider=provider, dimension=config.stub_dimension if provider == "local" else None
    )
    reranker = Qwen3Reranker(provider=provider)
    started = time.perf_counter()
    report = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "mode": config.mode,
        "purpose": "offline_regression_only"
        if config.mode == "stub"
        else "retrieval_algorithm_comparison",
        "config": asdict(config),
        "models": _model_identity(config, embedder, reranker),
        "backend": "InMemoryVectorStore_exact_L2_and_BM25",
        "python": platform.python_version(),
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "corpus_sha256": digest(corpus),
        "corpus_size": len(corpus),
        "samples": len(samples),
        "source_hashes": _source_hashes(),
        "annotations": {
            sample["id"]: sample.get("annotation", {"status": "unspecified"}) for sample in samples
        },
        "quality_claim_eligible": config.mode == "remote"
        and all(
            sample.get("annotation", {}).get("status") == "human_reviewed"
            and sample.get("source_kind") == "real"
            for sample in samples
        ),
        "metric_notes": {
            "hit_at_k": "Mean of any gold appearing in the first k retrieved chunks.",
            "recall_at_k": "Macro mean of |retrieved[:k] intersect gold| / |gold|; gold must include all annotated relevant chunks.",
            "failure_policy": "Failed retrieval attempts contribute zero for answerable queries; no silent fallback.",
            "unanswerable": "Empty gold is excluded from Hit/Recall; retrieval alone cannot score refusal correctness.",
            "latency": "Shared per-query stages timed once; each method sums its stages, including query embedding, excluding corpus indexing. P50/P95 use nearest-rank and include failures.",
        },
        "limitations": [
            "No Milvus/ANN or persistence benchmark.",
            "No generation, citation support or agent task quality is evaluated.",
            "Human-review metadata is asserted by dataset authors, not independently authenticated.",
        ],
        "rows": [],
    }
    report["configuration_sha256"] = digest(
        {"config": report["config"], "models": report["models"], "backend": report["backend"]}
    )
    calls = {"embedding_batches": 0, "embedding_texts": 0, "reranker_calls": 0}
    store = InMemoryVectorStore()
    index_started = time.perf_counter()
    try:
        for start in range(0, len(corpus), config.embedding_batch_size):
            batch = corpus[start : start + config.embedding_batch_size]
            calls["embedding_batches"] += 1
            calls["embedding_texts"] += len(batch)
            vectors = embedder.embed([document["content"] for document in batch])
            store.insert(
                [
                    {**document, "dense_vector": vector}
                    for document, vector in zip(batch, vectors, strict=True)
                ]
            )
    except Exception as exc:
        report.update(status="failed", indexing_error=type(exc).__name__)
    report["indexing_ms"] = (time.perf_counter() - index_started) * 1000
    if report["status"] == "completed":
        for repeat in range(config.repeats):
            for sample in samples:
                stages, rankings, errors = {}, {}, {}

                def timed(name, operation):
                    begin = time.perf_counter()
                    try:
                        return operation()
                    finally:
                        stages[name] = (time.perf_counter() - begin) * 1000

                try:
                    calls["embedding_batches"] += 1
                    calls["embedding_texts"] += 1
                    vector = timed("query_embedding", lambda: embedder.embed([sample["query"]])[0])
                    rankings["dense"] = timed(
                        "dense", lambda: store.search(vector, top_k=config.dense_top_k)
                    )
                except Exception as exc:
                    errors.update({method: type(exc).__name__ for method in METHODS})
                if "dense" in rankings:
                    try:
                        sparse = timed(
                            "bm25",
                            lambda: store.keyword_search(
                                sample["query"], top_k=config.sparse_top_k
                            ),
                        )
                        rankings["hybrid"] = timed(
                            "rrf", lambda: rrf_fusion([rankings["dense"], sparse], k=config.rrf_k)
                        )
                    except Exception as exc:
                        errors.update(
                            {method: type(exc).__name__ for method in ("hybrid", "hybrid_rerank")}
                        )
                if "hybrid" in rankings:
                    try:
                        candidates = rankings["hybrid"][: config.rerank_top_n]
                        calls["reranker_calls"] += 1
                        ranked = timed(
                            "rerank",
                            lambda: reranker.rerank(
                                sample["query"],
                                [item["content"] for item in candidates],
                                top_k=min(max(KS), len(candidates)),
                            ),
                        )
                        indices = [item.get("index") for item in ranked]
                        if (
                            len(indices) != min(max(KS), len(candidates))
                            or any(
                                type(i) is not int or not 0 <= i < len(candidates) for i in indices
                            )
                            or len(set(indices)) != len(indices)
                        ):
                            raise ValueError("invalid reranker ranking")
                        rankings["hybrid_rerank"] = [candidates[i] for i in indices]
                    except Exception as exc:
                        errors["hybrid_rerank"] = type(exc).__name__
                for method in METHODS:
                    names = ["query_embedding", "dense"]
                    if method != "dense":
                        names += ["bm25", "rrf"]
                    if method == "hybrid_rerank":
                        names.append("rerank")
                    ids = [item["id"] for item in rankings.get(method, [])[: max(KS)]]
                    report["rows"].append(
                        {
                            "sample_id": sample["id"],
                            "repeat": repeat + 1,
                            "method": method,
                            "query": sample["query"],
                            "gold_ids": sample["gold_ids"],
                            "retrieved_ids": ids,
                            "status": "failed" if method in errors else "completed",
                            "error_type": errors.get(method),
                            "metrics": ranking_metrics(ids, sample["gold_ids"]),
                            "latency_ms": sum(stages.get(name, 0) for name in names),
                            "stages_ms": {name: stages[name] for name in names if name in stages},
                        }
                    )
    if any(row["status"] == "failed" for row in report["rows"]):
        report["status"] = "partial_failure"
    report["methods"] = _summarize(report["rows"])
    report["client_calls"] = calls
    report["client_calls_note"] = (
        "Logical client calls; remote transport retries may make more HTTP attempts. No generation calls."
    )
    report["wall_time_ms"] = (time.perf_counter() - started) * 1000
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument(
        "--mode",
        choices=("stub", "remote"),
        default="stub",
        help="remote sends real embedding/reranker requests using configured services",
    )
    parser.add_argument(
        "--output", type=Path, help="new JSON artifact; existing files are never overwritten"
    )
    for field, default in asdict(EvaluationConfig()).items():
        if field != "mode":
            parser.add_argument("--" + field.replace("_", "-"), type=int, default=default)
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("output already exists; choose a new experiment path")
    config = EvaluationConfig(
        **{field: getattr(args, field) for field in asdict(EvaluationConfig())}
    )
    report = evaluate(args.dataset, corpus_path=args.corpus, config=config)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    print(rendered)
    if report["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
