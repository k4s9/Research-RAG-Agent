"""Acceptance safeguards, using scripted HTTP only; no real task-quality claims."""

from copy import deepcopy
import json

import httpx
import pytest

from scripts.run_minimal_live import record_http
from scripts.run_report_closure import NO_INFERENCE, require_delivery


def delivery():
    dimensions = ["method", "experimental settings", "limitations"]
    report = dict(
        title="Comparison", summary="Limited evidence", documents=["A", "B"],
        dimensions=dimensions,
        claims=[dict(document_id=d, dimension=dimension, statement="Measured configuration.",
                     verdict="supported", source_ids=[sid], quotes=["8 GB" if d == "A" else "16 GB"])
                for d, sid in [("A", "S1"), ("B", "S2")] for dimension in dimensions],
        recommendation="A fits the memory limit [S1].", incomparable=["Different datasets"],
        unresolved=["Energy not measured"], change_summary="Memory limit changed to 12 GB.",
    )
    artifact = dict(id="child", run_id="run", session_id="session", parent_report_id="parent",
        structured=report, evidence={
            "S1": dict(document_id="A", content="8 GB"),
            "S2": dict(document_id="B", content="16 GB"),
        })
    return (
        dict(run_id="run", status="insufficient_evidence", state={"report_id": "child"}),
        artifact,
        dict(document_ids=["A", "B"], task_type="revise", parent_report_id="parent", session_id="session"),
    )


def test_delivered_unknowns_accepted_without_modifying_model_fields():
    run, artifact, request = delivery()
    original = deepcopy(artifact)
    require_delivery(run, artifact, request)
    assert artifact == original


@pytest.mark.parametrize("fault", ["failed_run", "no_report", "missing_section", "empty_gaps",
                                  "cross_document_quote", "missing_change", "wrong_parent"])
def test_status_or_exact_quotes_alone_cannot_pass_closure(fault):
    run, artifact, request = delivery()
    if fault == "failed_run":
        run["status"] = "budget_exceeded"
    elif fault == "no_report":
        artifact = None
    elif fault == "missing_section":
        del artifact["structured"]["incomparable"]
    elif fault == "empty_gaps":
        artifact["structured"]["unresolved"] = []
    elif fault == "cross_document_quote":
        artifact["structured"]["claims"][0].update(source_ids=["S2"], quotes=["16 GB"])
    elif fault == "missing_change":
        artifact["structured"]["change_summary"] = ""
    elif fault == "wrong_parent":
        artifact["parent_report_id"] = "unrelated"
    with pytest.raises((AssertionError, ValueError)):
        require_delivery(run, artifact, request)


@pytest.mark.asyncio
async def test_batch_limit_spans_comparison_and_revision_and_retains_full_response(tmp_path):
    payload = dict(model="Qwen3.8-27B", reasoning_effort="low", temperature=0,
                   messages=[{"role": "user", "content": "test"}], max_tokens=2048)
    raw = dict(choices=[dict(finish_reason="tool_calls", message={"content": "x" * 8000})],
               usage=dict(prompt_tokens=20, completion_tokens=2048))
    stage = ["comparison"]
    with record_http(tmp_path, stage, limits=dict(generation=1, embedding=0, reranker=0,
                                                total_tokens=5000)) as ledger:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=raw))) as client:
            await client.post("http://test/v1/chat/completions", json=payload)
            stage[0] = "revision"
            with pytest.raises(RuntimeError, match="generation request budget"):
                await client.post("http://test/v1/chat/completions", json=payload)
        assert ledger["generation"] == 1
        assert ledger["accounted_tokens"] == 2068
    saved = json.loads((tmp_path / "http-generation-01.json").read_text())
    assert json.loads(saved["raw_response"]) == raw
    assert saved["finish_reasons"] == ["tool_calls"]


@pytest.mark.asyncio
async def test_reopening_disallows_inference_before_transport(tmp_path):
    def unexpected(request):
        raise AssertionError("inference must not reach transport")

    with record_http(tmp_path, ["reopen"], limits=NO_INFERENCE):
        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
            with pytest.raises(RuntimeError, match="generation request budget"):
                await client.post("http://test/v1/chat/completions", json={})
    assert json.loads((tmp_path / "usage.json").read_text())["generation"] == 0
