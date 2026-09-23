import asyncio
import json
from copy import deepcopy

import pytest

from src.core.agent.orchestrator import AgentOrchestrator
from src.core.agent.runtime import EvidenceRegistry

pytestmark = pytest.mark.unit


def call(name, args):
    return {
        "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}
    }


def envelope(*calls, text=None, usage=None):
    return {"message": {"content": text, "tool_calls": list(calls)}, "usage": usage}


class Model:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.messages = []

    def generate_with_tools(self, prompt, **kwargs):
        self.messages.append(deepcopy(kwargs["tool_messages"]))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class Registry:
    def __init__(self):
        self.calls = []

    async def execute(self, name, args, **kwargs):
        self.calls.append((name, args))
        key = args.get("query", "A")
        return {
            "results": [
                {
                    "id": key,
                    "document_id": key,
                    "content": f"{key} costs 8 GB.",
                    "filename": f"{key}.md",
                    "document_hash": f"hash-{key}",
                }
            ]
        }


async def execute(model, registry=None, **kwargs):
    registry = registry or Registry()
    return await AgentOrchestrator(
        llm_client=model, searcher=object(), tool_registry=registry
    ).handle_message("Compare the sources", ["p"], "s", **kwargs)


@pytest.mark.asyncio
async def test_source_identity_survives_two_searches_repeat_read_and_final_answer():
    model = Model(
        envelope(call("search_knowledge", {"query": "A"})),
        envelope(call("search_knowledge", {"query": "B"})),
        envelope(call("search_knowledge", {"query": "A"})),
        envelope(text="B [S2], A [S1]"),
    )
    registry = Registry()
    result = await execute(model, registry)
    assert {c["source_id"]: c["document_id"] for c in result["citations"]} == {"S2": "B", "S1": "A"}
    assert result["invalid_citation_ids"] == []
    assert len(registry.calls) == 2
    assert result["tool_trace"][2]["cached"]
    assert json.loads(model.messages[2][-1]["content"])["results"][0]["source_id"] == "S2"
    for messages in model.messages:
        assert messages[0]["role"] == "system"
        assert "never renumber" in messages[0]["content"]
        assert messages[1]["content"] == "Compare the sources"
        pending = set()
        for message in messages:
            if message["role"] == "assistant":
                pending.update(c["id"] for c in message.get("tool_calls", []))
            elif message["role"] == "tool":
                pending.remove(message["tool_call_id"])
        assert not pending


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["{oops", "[]", "null", "true"])
async def test_bad_arguments_are_observations_and_can_be_corrected(bad):
    model = Model(
        envelope(call("search_knowledge", bad)),
        envelope(call("search_knowledge", {"query": "A"})),
        envelope(text="A [S1]"),
    )
    result = await execute(model)
    assert result["run_status"] == "completed"
    assert result["tool_trace"][0]["raw_arguments"] == bad
    assert result["tool_trace"][0]["error"]
    assert result["usage"]["tool_calls"] == 2


@pytest.mark.asyncio
async def test_missing_report_sections_are_rejected_then_corrected():
    report = dict(title="Comparison", summary="A needs 8 GB.", documents=["A"],
                  dimensions=["memory"], claims=[dict(dimension="memory", document_id="A",
                  statement="A costs 8 GB.", verdict="supported", source_ids=["S1"],
                  quotes=["A costs 8 GB."])], recommendation="Use A in the reported setting.")
    model = Model(envelope(call("search_knowledge", {"query": "A"})),
                  envelope(call("submit_report", report)),
                  envelope(call("submit_report", {**report, "incomparable": [], "unresolved": []})))
    result = await execute(model, strategy="b2", task_type="compare")
    rejected = result["tool_trace"][1]
    assert rejected["status"] == "failed"
    assert "incomparable" in rejected["error"] and "unresolved" in rejected["error"]
    assert result["run_status"] == "completed"
    assert result["report"]["incomparable"] == []
    assert result["usage"]["model_calls"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [{"model_calls": 2}, {"tool_calls": 2}])
async def test_valid_report_with_gaps_is_saved_when_no_followup_call_remains(budget):
    report = dict(title="Comparison", summary="A needs 8 GB.", documents=["A"],
                  dimensions=["memory"], claims=[dict(dimension="memory", document_id="A",
                  statement="A costs 8 GB.", verdict="supported", source_ids=["S1"],
                  quotes=["A costs 8 GB."])], recommendation="Use A in the reported setting.",
                  incomparable=[], unresolved=["Energy was not measured."])
    model = Model(envelope(call("search_knowledge", {"query": "A"})),
                  envelope(call("submit_report", report)))
    result = await execute(model, strategy="b2", task_type="compare", budget=budget)
    assert result["run_status"] == "insufficient_evidence"
    assert result["report"]["unresolved"] == report["unresolved"]
    assert result["tool_trace"][-1]["result"] == {"accepted": True}
    assert result["usage"]["model_calls"] == result["usage"]["tool_calls"] == 2


@pytest.mark.asyncio
async def test_one_model_turn_cannot_bypass_tool_limit():
    registry = Registry()
    model = Model(envelope(*(call("search_knowledge", {"query": str(i)}) for i in range(9))))
    result = await execute(model, registry, budget={"tool_calls": 2})
    assert len(registry.calls) == 2
    assert result["run_status"] == "budget_exceeded"
    assert result["termination_reason"] == "tool_calls"
    assert len(result["evidence"]) == 2
    assert result["usage"]["model_calls"] == 1


@pytest.mark.asyncio
async def test_model_failure_keeps_evidence_trace_and_unknown_cost_without_fallback():
    result = await execute(
        Model(envelope(call("search_knowledge", {"query": "A"})), RuntimeError("offline"))
    )
    assert result["run_status"] == "failed"
    assert len(result["tool_trace"]) == 1
    assert result["evidence"]["S1"]["document_id"] == "A"
    assert result["usage"]["model_calls"] == 2
    assert result["usage"]["cost"] is None
    assert result["usage"]["unknown_usage_calls"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "budget,reason",
    [
        ({"model_calls": 1}, "model_calls"),
        ({"context_tokens": 1}, "context_tokens"),
        ({"total_tokens": 1}, "total_tokens"),
        ({"result_chars": 10}, "tool_result_chars"),
    ],
)
async def test_independent_budget_guards(budget, reason):
    result = await execute(Model(envelope(call("search_knowledge", {"query": "A"}))), budget=budget)
    assert result["run_status"] == "budget_exceeded"
    assert result["termination_reason"] == reason


@pytest.mark.asyncio
async def test_active_deadline_and_cancellation():
    class SlowRegistry(Registry):
        async def execute(self, *args, **kwargs):
            await asyncio.sleep(10)

    result = await execute(
        Model(envelope(call("search_knowledge", {"query": "A"}))),
        SlowRegistry(),
        budget={"active_seconds": 0.05},
    )
    assert result["run_status"] == "budget_exceeded"
    assert result["termination_reason"] == "active_time"


def test_range_identity_exact_offsets_and_content_snapshot():
    registry = EvidenceRegistry()
    result = registry.register_result(
        {
            "document_id": "d",
            "document_hash": "v1",
            "chunks": [
                {"chunk_id": "c", "content": "abc", "char_start": 0, "char_end": 3},
                {"chunk_id": "c", "content": "def", "char_start": 3, "char_end": 6},
            ],
        }
    )
    assert [c["source_id"] for c in result["chunks"]] == ["S1", "S2"]
    assert registry.register_result(result)["chunks"][1]["source_id"] == "S2"
    reloaded = EvidenceRegistry(registry.sources)
    assert reloaded.sources["S1"]["content"] == "abc"
    changed = reloaded.register(
        {"document_id": "d", "document_hash": "v2", "id": "c", "content": "abc"}
    )
    assert changed["source_id"] == "S3"


@pytest.mark.asyncio
async def test_model_context_omits_diagnostics_but_trace_and_evidence_keep_provenance():
    class VerboseRegistry(Registry):
        async def execute(self, *args, **kwargs):
            result = await super().execute(*args, **kwargs)
            result["results"][0].update(dense_score=0.7, rrf_rank=1, rerank_score=0.8)
            return result

    model = Model(envelope(call("search_knowledge", {"query": "A"})), envelope(text="A [S1]"))
    result = await execute(model, VerboseRegistry())
    shown = json.loads(model.messages[1][-1]["content"])["results"][0]
    assert shown["content"] == result["evidence"]["S1"]["content"]
    assert shown["source_id"] == "S1"
    assert shown["document_id"] == "A"
    assert "dense_score" not in shown
    assert result["tool_trace"][0]["result"]["results"][0]["dense_score"] == 0.7
    assert result["evidence"]["S1"]["document_hash"] == "hash-A"


@pytest.mark.asyncio
@pytest.mark.parametrize("finish,content,status,reason", [
    ("stop", "", "failed", "empty_model_response"),
    ("length", "Partial answer", "budget_exceeded", "output_tokens"),
])
async def test_empty_or_truncated_model_output_is_not_success(finish, content, status, reason):
    response = {**envelope(text=content, usage={"prompt_tokens": 20, "completion_tokens": 10}),
                "finish_reason": finish}
    result = await execute(Model(response))
    assert result["run_status"] == status
    assert result["termination_reason"] == reason
    assert result["checkpoint"]["model_trace"][0]["finish_reason"] == finish
    assert result["usage"]["completion_tokens"] == 10


@pytest.mark.asyncio
async def test_b0_retrieval_failure_retains_attempt_trace_without_fallback():
    class Searcher:
        def search(self, **kwargs):
            raise RuntimeError("retrieval unavailable")

    model = Model()
    result = await AgentOrchestrator(llm_client=model, searcher=Searcher()).handle_message(
        "Compare", ["p"], "s", strategy="b0",
    )
    assert result["run_status"] == "failed"
    assert result["usage"]["model_calls"] == 0
    assert result["usage"]["tool_calls"] == 1
    assert result["tool_trace"][0]["arguments"]["query"] == "Compare"
    assert result["tool_trace"][0]["status"] == "failed"
    assert "retrieval unavailable" in result["tool_trace"][0]["error"]
