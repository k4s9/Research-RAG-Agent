import asyncio
import json
from copy import deepcopy

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from test_chat_sessions import ChatHarness

from src.core.agent.service import workers
from src.db.models import AgentRun, ChatSession, Conversation, ReportArtifact

pytestmark = pytest.mark.unit


def tool(name, args):
    return {
        "message": {"tool_calls": [{"function": {"name": name, "arguments": json.dumps(args)}}]}
    }


def report_payload():
    return dict(
        title="Memory comparison",
        summary="Evidence comparison.",
        documents=["A", "B"],
        dimensions=["memory"],
        claims=[
            dict(
                dimension="memory",
                document_id=d,
                statement=f"{d} uses {n} GB.",
                verdict="supported",
                source_ids=[f"S{i}"],
                quotes=[f"{n} GB"],
            )
            for i, (d, n) in enumerate([("A", 8), ("B", 16)], 1)
        ],
        recommendation="For 8 GB memory choose A [S1].",
        incomparable=[],
        unresolved=[],
    )


class Model:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = 0

    def generate_with_tools(self, prompt, **kwargs):
        self.calls += 1
        try:
            return next(self.responses)
        except StopIteration:
            raise AssertionError("unexpected extra model request") from None


class Registry:
    async def execute(self, name, args, **kwargs):
        return {
            "results": [
                dict(
                    id=d,
                    document_id=d,
                    filename=f"{d}.md",
                    content=f"{d} uses {n} GB.",
                    document_hash=f"hash-{d}",
                )
                for d, n in [("A", 8), ("B", 16)]
            ]
        }


async def terminal(client, run_id):
    for _ in range(300):
        data = (await client.get(f"/api/v1/chat/runs/{run_id}")).json()
        if data["status"] != "running":
            return data
        await asyncio.sleep(0.01)
    raise AssertionError("run did not terminate")


@pytest.mark.asyncio
async def test_report_saved_once_restart_resume_revision_preserves_sources(tmp_path):
    model = Model(
        tool("search_knowledge", {"query": "memory"}), tool("submit_report", report_payload())
    )
    stack = await ChatHarness(tmp_path / "report.db", llm=model, registry=Registry()).start()
    try:
        result = await stack.client.post(
            "/api/v1/chat/runs",
            json=dict(
                session_id="s",
                project_ids=["p"],
                message="Compare A B",
                task_type="compare",
                strategy="b2",
            ),
        )
        assert result.status_code == 202, result.text
        run_id = result.json()["run_id"]
        run = await terminal(stack.client, run_id)
        assert run["status"] == "completed", run
        report_id = run["state"]["report_id"]
        document = (await stack.client.get(f"/api/v1/chat/reports/{report_id}")).json()
        assert document["evidence"]["S2"]["document_id"] == "B"
        assert "SHA256" in document["markdown"]
        download = await stack.client.get(f"/api/v1/chat/reports/{report_id}/download")
        assert download.text == document["markdown"]
        await stack.restart()
        for _ in range(2):
            resumed = await stack.client.post(f"/api/v1/chat/runs/{run_id}/resume", json={})
            assert resumed.json()["report_id"] == report_id
        revision = report_payload()
        revision["change_summary"] = "Keep only A and its existing source."
        revision["documents"] = ["A"]
        revision["claims"] = revision["claims"][:1]
        stack.llm = Model(tool("submit_report", revision))
        revised = await stack.client.post(
            "/api/v1/chat/message",
            json=dict(
                session_id="s",
                project_ids=["p"],
                message="Only A",
                task_type="revise",
                strategy="b2",
            ),
        )
        assert revised.status_code == 200, revised.text
        revised_doc = (
            await stack.client.get("/api/v1/chat/reports/" + revised.json()["report_id"])
        ).json()
        assert revised_doc["parent_report_id"] == report_id
        assert revised_doc["evidence"]["S1"] == document["evidence"]["S1"]
        with stack.sessions() as db:
            assert len(db.scalars(select(ReportArtifact)).all()) == 2
            assert len(db.scalars(select(Conversation)).all()) == 4
    finally:
        await stack.stop()


@pytest.mark.asyncio
async def test_clarify_restart_remaining_budget_and_no_duplicate_resume(tmp_path):
    model = Model(tool("ask_user", {"question": "What memory limit?"}))
    stack = await ChatHarness(tmp_path / "clarify.db", llm=model, registry=Registry()).start()
    try:
        response = await stack.client.post(
            "/api/v1/chat/message",
            json=dict(
                session_id="s",
                project_ids=["p"],
                message="Compare",
                task_type="compare",
                strategy="b2",
                budget={"model_calls": 3},
            ),
        )
        assert response.status_code == 200, response.text
        assert response.json()["run_status"] == "waiting_user"
        run_id = response.json()["run_id"]
        busy = await stack.client.post(
            "/api/v1/chat/message",
            json=dict(session_id="s", project_ids=["p"], message="Competing"),
        )
        assert busy.status_code == 409
        await stack.restart()
        before = (await stack.client.get(f"/api/v1/chat/runs/{run_id}")).json()["state"][
            "execution"
        ]
        stack.llm = Model(
            tool("search_knowledge", {"query": "memory"}), tool("submit_report", report_payload())
        )
        resumed = await stack.client.post(
            f"/api/v1/chat/runs/{run_id}/resume", json={"message": "8 GB"}
        )
        assert resumed.status_code == 202, resumed.text
        duplicate = await stack.client.post(
            f"/api/v1/chat/runs/{run_id}/resume", json={"message": "8 GB"}
        )
        assert duplicate.status_code in (409, 202)
        after = await terminal(stack.client, run_id)
        assert after["status"] == "completed", after
        execution = after["state"]["execution"]
        assert execution["usage"]["model_calls"] == 3
        assert execution["usage"]["active_seconds"] >= before["usage"]["active_seconds"]
        assert execution["budget"]["model_calls"] == 3
        with stack.sessions() as db:
            assert len(db.scalars(select(Conversation)).all()) == 4
    finally:
        await stack.stop()


@pytest.mark.asyncio
async def test_cancel_stops_model_and_prevents_report_writes(tmp_path):
    entered = asyncio.Event()

    class SlowModel:
        def generate_with_tools(self, *a, **k):
            raise AssertionError("async entry expected")

        async def acomplete(self, *a, **k):
            entered.set()
            await asyncio.sleep(30)
            return tool("submit_report", report_payload())

    stack = await ChatHarness(tmp_path / "cancel.db", llm=SlowModel(), registry=Registry()).start()
    try:
        created = await stack.client.post(
            "/api/v1/chat/runs",
            json=dict(
                session_id="s",
                project_ids=["p"],
                message="Compare",
                task_type="compare",
                strategy="b2",
            ),
        )
        run_id = created.json()["run_id"]
        await asyncio.wait_for(entered.wait(), 2)
        cancelled = await stack.client.post(f"/api/v1/chat/runs/{run_id}/cancel")
        assert cancelled.json()["status"] == "cancelled"
        await asyncio.sleep(0.02)
        assert run_id not in workers
        assert (
            await stack.client.post(f"/api/v1/chat/runs/{run_id}/resume", json={})
        ).status_code == 409
        with stack.sessions() as db:
            assert not db.scalars(select(ReportArtifact)).all()
            assert len(db.scalars(select(Conversation)).all()) == 1
    finally:
        await stack.stop()


@pytest.mark.asyncio
async def test_final_checkpoint_recovery_never_repeats_model_or_report(tmp_path):
    stack = await ChatHarness(tmp_path / "checkpoint.db", llm=Model(), registry=Registry()).start()
    try:
        # Simulate process death after final checkpoint, before artifact transaction.
        from src.core.agent.service import prepare_run
        from src.schemas.chat import ChatMessageRequest

        async for db in stack._database():
            run = await prepare_run(
                db,
                ChatMessageRequest(
                    session_id="s",
                    project_ids=["p"],
                    message="Compare",
                    task_type="compare",
                    strategy="b2",
                ),
            )
            from src.core.agent.orchestrator import AgentOrchestrator

            result = await AgentOrchestrator(
                llm_client=Model(
                    tool("search_knowledge", {"query": "memory"}),
                    tool("submit_report", report_payload()),
                ),
                searcher=object(),
                tool_registry=Registry(),
            ).handle_message("Compare", ["p"], "s", strategy="b2", task_type="compare")
            run.state = {**run.state, "execution": deepcopy(result["checkpoint"])}
            db.add(run)
            await db.commit()
            run_id = run.id
            break
        await stack.restart()
        response = await stack.client.post(f"/api/v1/chat/runs/{run_id}/resume", json={})
        assert response.status_code == 202, response.text
        finished = await terminal(stack.client, run_id)
        assert finished["status"] == "completed"
        assert stack.llm.calls == 0
        assert finished["state"]["report_id"]
    finally:
        await stack.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining_calls", [0, 1])
async def test_gap_comparison_and_revision_save_automatically_at_call_limit(tmp_path, remaining_calls):
    report = report_payload()
    report["incomparable"] = ["Different datasets."]
    report["unresolved"] = ["Energy was not measured."]
    model = Model(tool("search_knowledge", {"query": "memory"}), tool("submit_report", report))
    stack = await ChatHarness(tmp_path / "gap-closure.db", llm=model, registry=Registry()).start()
    try:
        response = await stack.client.post("/api/v1/chat/runs", json=dict(
            session_id="s", project_ids=["p"], message="Compare under 8 GB",
            task_type="compare", strategy="b2", budget={"model_calls": 2 + remaining_calls},
        ))
        assert response.status_code == 202, response.text
        parent_run = await terminal(stack.client, response.json()["run_id"])
        assert parent_run["status"] == "insufficient_evidence"
        parent_id = parent_run["state"]["report_id"]
        parent = (await stack.client.get(f"/api/v1/chat/reports/{parent_id}")).json()
        revised = deepcopy(report)
        revised["change_summary"] = "The available memory is now 12 GB."
        revised["recommendation"] = "At 12 GB, the measured A configuration fits [S1]."
        stack.llm = Model(tool("submit_report", revised))
        response = await stack.client.post("/api/v1/chat/runs", json=dict(
            session_id="s", project_ids=["p"], message="Revise the limit to 12 GB",
            task_type="revise", strategy="b2", parent_report_id=parent_id,
            budget={"model_calls": 1 + remaining_calls},
        ))
        assert response.status_code == 202, response.text
        child_run = await terminal(stack.client, response.json()["run_id"])
        assert child_run["status"] == "insufficient_evidence"
        child_id = child_run["state"]["report_id"]
        child = (await stack.client.get(f"/api/v1/chat/reports/{child_id}")).json()
        assert child["structured"] == revised
        assert child["parent_report_id"] == parent_id
        assert child["evidence"] == parent["evidence"]
        await stack.restart()
        for run, artifact in [(parent_run, parent), (child_run, child)]:
            assert (await stack.client.get(f"/api/v1/chat/reports/{artifact['id']}")).json() == artifact
            assert (await stack.client.get(f"/api/v1/chat/reports/{artifact['id']}/download")).text == artifact["markdown"]
            reopened = (await stack.client.get(f"/api/v1/chat/runs/{run['run_id']}")).json()
            assert reopened["state"]["execution"]["usage"] == run["state"]["execution"]["usage"]
        assert model.calls == 2
        assert stack.llm.calls == 1
        with stack.sessions() as db:
            assert len(db.scalars(select(ReportArtifact)).all()) == 2
    finally:
        await stack.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [True, False])
async def test_failed_report_transaction_is_visible_and_resumes_without_inference(tmp_path, background):
    report = report_payload()
    report["unresolved"] = ["Energy was not measured."]
    model = Model(tool("search_knowledge", {"query": "memory"}), tool("submit_report", report))
    stack = await ChatHarness(tmp_path / "save-failure.db", llm=model, registry=Registry()).start()

    def break_insert(mapper, connection, target):
        connection.exec_driver_sql("INSERT INTO missing_report_table VALUES (1)")

    event.listen(ReportArtifact, "before_insert", break_insert)
    try:
        request = dict(session_id="s", project_ids=["p"], message="Compare",
                       task_type="compare", strategy="b2", budget={"model_calls": 2})
        if background:
            response = await stack.client.post("/api/v1/chat/runs", json=request)
            assert response.status_code == 202
            run_id = response.json()["run_id"]
        else:
            with pytest.raises(OperationalError):
                await stack.client.post("/api/v1/chat/message", json=request)
            with stack.sessions() as db:
                run_id = db.scalars(select(AgentRun)).one().id
        failed = await terminal(stack.client, run_id)
        assert failed["status"] == "failed"
        assert failed["error"] == "execution_failed:OperationalError"
        assert failed["finished_at"]
        checkpoint = failed["state"]["execution"]
        assert checkpoint["status"] == "insufficient_evidence"
        assert checkpoint["report"] == report
        assert not failed["state"].get("report_id")
        with stack.sessions() as db:
            assert db.get(ChatSession, "s").active_run_id is None
            assert not db.scalars(select(ReportArtifact)).all()
            assert len(db.scalars(select(Conversation)).all()) == 1
    finally:
        event.remove(ReportArtifact, "before_insert", break_insert)
        await stack.stop()

    await stack.start()
    try:
        resumed = await stack.client.post(f"/api/v1/chat/runs/{run_id}/resume", json={})
        assert resumed.status_code == 202
        finished = await terminal(stack.client, run_id)
        assert finished["status"] == "insufficient_evidence"
        assert finished["error"] is None
        assert finished["state"]["execution"]["usage"] == checkpoint["usage"]
        assert model.calls == 2
        with stack.sessions() as db:
            assert len(db.scalars(select(ReportArtifact)).all()) == 1
            assert len(db.scalars(select(Conversation)).all()) == 2
    finally:
        await stack.stop()
