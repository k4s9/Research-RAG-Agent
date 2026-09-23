import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
spec = importlib.util.spec_from_file_location(
    "task_evaluator", Path(__file__).resolve().parents[2] / "scripts/evaluate_tasks.py"
)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_runtime_identity_rejects_local_models():
    config = dict(model="qwen", code_hashes={"src/a.py": "abc"}, conditions={
        "runtime": {"embedding_provider": "remote", "vector_backend": "memory"}
    })
    with pytest.raises(ValueError, match="embedding_provider"):
        evaluation.verify_runtime(config, dict(
            model="qwen", code_hashes=config["code_hashes"],
            embedding_provider="local", vector_backend="memory",
        ))


def test_summary_keeps_unknown_failure_budget_and_excludes_unattempted_latency(tmp_path):
    samples = tmp_path / "samples.jsonl"
    samples.write_text("\n".join(json.dumps(row) for row in [
        dict(task_id="a", repeat=0, system="b0", turns=[], success=None,
             error="network failure", attempted=True, latency_seconds=10,
             reservations=[dict(model_calls=6, tokens=128000, usage_unknown=True)]),
        dict(task_id="b", repeat=0, system="b0", turns=[], success=None,
             error="budget exhausted", attempted=False, latency_seconds=None, reservations=[]),
    ]))
    evaluation.summarize(samples, tmp_path)
    metrics = json.loads((tmp_path / "metrics.json").read_text())["b0"]
    assert metrics["n"] == 2
    assert metrics["attempted"] == 1
    assert metrics["failures"] == 2
    assert metrics["p50_seconds"] == metrics["p95_seconds"] == 10
    assert metrics["unobserved_reserved_tokens"] == 128000
    assert metrics["unobserved_reserved_model_calls"] == 6
    assert metrics["cost"] is None
    assert metrics["success_rate"] == 0


def test_unjudged_completion_is_not_success(tmp_path):
    samples = tmp_path / "samples.jsonl"
    samples.write_text(json.dumps(dict(task_id="a", repeat=0, system="b1", turns=[],
                                      error=None, success=None, latency_seconds=3)))
    evaluation.summarize(samples, tmp_path)
    assert json.loads((tmp_path / "metrics.json").read_text())["b1"]["success_rate"] is None
