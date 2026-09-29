import json

import pytest
import requests

from scripts.evaluate_retrieval import EvaluationConfig, evaluate, load_dataset, ranking_metrics
from src.config.settings import settings
from src.core.retrieval.reranker import Qwen3Reranker

pytestmark = pytest.mark.unit


def dataset(tmp_path, samples=None, documents=None):
    documents = documents or [
        {"id": "a", "content": "Verified calibration before the experiment."},
        {"id": "b", "content": "Calibration is checked with an independent reference."},
        {"id": "c", "content": "Reproducible timing is recorded after each run."},
        {"id": "d", "content": "The building opens at nine."},
        {"id": "e", "content": "Equipment must remain dry."},
        {"id": "f", "content": "Data is archived weekly."},
    ]
    samples = samples or [{"id": "q1", "query": "verified calibration", "gold_ids": ["a", "b"]}]
    questions, corpus = tmp_path / "questions.jsonl", tmp_path / "corpus.jsonl"
    questions.write_text("\n".join(json.dumps(row) for row in samples))
    corpus.write_text("\n".join(json.dumps(row) for row in documents))
    return questions, corpus


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("unexpected network request")

    monkeypatch.setattr(requests, "post", reject)


def test_hit_and_multigold_recall_are_distinct_and_no_answer_is_not_scored():
    values = ranking_metrics(["x", "a", "a"], ["a", "b"])
    assert values["hit_at_3"] == 1
    assert values["recall_at_3"] == 0.5
    assert values["hit_at_1"] == values["recall_at_1"] == 0
    assert all(value is None for value in ranking_metrics(["a"], []).values())


def test_same_corpus_and_rankings_reproduce_without_remote_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "remote")
    monkeypatch.setattr(settings, "reranker_provider", "remote")
    questions, corpus = dataset(tmp_path)
    config = EvaluationConfig(repeats=2)
    first = evaluate(questions, corpus_path=corpus, config=config)
    second = evaluate(questions, corpus_path=corpus, config=config)
    assert first["status"] == "completed"
    assert first["purpose"] == "offline_regression_only"
    assert not first["quality_claim_eligible"]
    assert first["models"]["embedding"]["algorithm"] == "sha256_token_hash"
    assert first["corpus_size"] == 6
    assert len(first["rows"]) == 6
    assert first["client_calls"] == {
        "embedding_batches": 3,
        "embedding_texts": 8,
        "reranker_calls": 2,
    }
    for key in ("corpus_sha256", "dataset_sha256", "configuration_sha256", "source_hashes"):
        assert first[key] == second[key]
    assert [row["retrieved_ids"] for row in first["rows"]] == [
        row["retrieved_ids"] for row in second["rows"]
    ]
    for row in first["rows"]:
        assert row["latency_ms"] == sum(row["stages_ms"].values())
    for summary in first["methods"].values():
        assert summary["attempts"] == 2
        assert summary["failed"] == 0
        assert summary["hit_at_1"] >= summary["recall_at_1"]


def test_incompatible_inline_corpora_and_unknown_gold_are_rejected(tmp_path):
    questions = tmp_path / "inline.jsonl"
    rows = [
        {
            "id": str(i),
            "query": "q",
            "gold_ids": ["a"],
            "documents": [{"id": "a", "content": content}],
        }
        for i, content in enumerate(("original", "different"))
    ]
    questions.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match="conflicting"):
        load_dataset(questions)
    questions, corpus = dataset(tmp_path, samples=[{"query": "q", "gold_ids": ["missing"]}])
    with pytest.raises(ValueError, match="unknown"):
        load_dataset(questions, corpus)


def test_pending_labels_cannot_be_treated_as_no_answer_or_human_review(tmp_path):
    questions, corpus = dataset(tmp_path, samples=[{"query": "q", "gold_ids": []}])
    with pytest.raises(ValueError, match="answerable=false"):
        load_dataset(questions, corpus)
    questions, corpus = dataset(
        tmp_path,
        samples=[{"query": "q", "gold_ids": ["a"], "annotation": {"status": "human_reviewed"}}],
    )
    with pytest.raises(ValueError, match="reviewer"):
        load_dataset(questions, corpus)


def test_reranker_failure_is_counted_without_silent_rrf_fallback(tmp_path, monkeypatch):
    questions, corpus = dataset(
        tmp_path,
        samples=[
            {"id": "answer", "query": "calibration", "gold_ids": ["a", "b"]},
            {
                "id": "none",
                "query": "unreported energy consumption",
                "gold_ids": [],
                "answerable": False,
            },
        ],
    )

    def fail(*args, **kwargs):
        raise RuntimeError("service URL and secrets must not be serialized")

    monkeypatch.setattr(Qwen3Reranker, "rerank", fail)
    report = evaluate(questions, corpus_path=corpus)
    assert report["status"] == "partial_failure"
    assert report["methods"]["dense"]["failed"] == 0
    failed = report["methods"]["hybrid_rerank"]
    assert failed["failed"] == 2
    assert failed["answerable_attempts"] == failed["unanswerable_attempts"] == 1
    assert failed["hit_at_5"] == failed["recall_at_5"] == 0
    assert all(
        row["retrieved_ids"] == [] for row in report["rows"] if row["method"] == "hybrid_rerank"
    )
    assert "service URL" not in json.dumps(report)


def test_remote_mode_uses_configured_models_and_records_effective_identity(tmp_path, monkeypatch):
    questions, corpus = dataset(tmp_path)
    monkeypatch.setattr(settings, "embedding_model", "test-embedding-model")
    monkeypatch.setattr(settings, "embedding_dimension", 2)
    monkeypatch.setattr(settings, "reranker_model", "test-rerank-model")
    monkeypatch.setattr(settings, "embedding_api_key", "do-not-serialize")
    monkeypatch.setattr(settings, "reranker_api_key", "do-not-serialize")
    payloads = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def post(url, *, json, **kwargs):
        payloads.append(json)
        if "input" in json:
            return Response(
                {
                    "data": [
                        {"index": i, "embedding": [1.0, 0.0]} for i, _ in enumerate(json["input"])
                    ]
                }
            )
        return Response(
            {
                "results": [
                    {"index": i, "relevance_score": 1 - i * 0.1} for i in range(json["top_n"])
                ]
            }
        )

    monkeypatch.setattr(requests, "post", post)
    report = evaluate(questions, corpus_path=corpus, config=EvaluationConfig(mode="remote"))
    assert report["status"] == "completed"
    assert {payload["model"] for payload in payloads} == {
        "test-embedding-model",
        "test-rerank-model",
    }
    assert report["models"]["embedding"]["requested_model"] == "test-embedding-model"
    assert report["models"]["reranker"]["requested_model"] == "test-rerank-model"
    assert not report["models"]["deployment_identity_verified"]
    assert not report["quality_claim_eligible"]
    assert "do-not-serialize" not in json.dumps(report)
