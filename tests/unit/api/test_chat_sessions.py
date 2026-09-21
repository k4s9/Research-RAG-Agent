import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.api.dependencies import get_orchestrator
from src.core.agent.orchestrator import AgentOrchestrator
from src.db.models import AgentRun, AgentStep, Base, Conversation
from src.db.postgres import get_db
from src.main import app

pytestmark = pytest.mark.unit


class SessionAdapter:
    """Minimal AsyncSession-shaped adapter over a sync SQLite session."""

    def __init__(self, session: object) -> None:
        self.session = session

    def add(self, instance: object) -> None:
        self.session.add(instance)

    async def get(self, model: type, identifier: str) -> object:
        return self.session.get(model, identifier)

    async def execute(self, statement: object) -> object:
        return self.session.execute(statement)

    async def commit(self) -> None:
        self.session.commit()

    async def refresh(self, instance: object) -> None:
        self.session.refresh(instance)


class FakeSearcher:
    def search(self, **kwargs: object) -> list[dict]:
        return [{"id": "chunk-1", "score": 0.9}]


async def fake_hydrator(results: list[dict], project_ids: list[str]) -> list[dict]:
    return [
        {
            **results[0],
            "content": "Flash Attention 显存占用更低。",
            "filename": "attention.pdf",
            "source": "attention.pdf",
            "locator": {"file_type": "pdf", "page_start": 2, "page_end": 2},
        },
    ]


class RecordingLLM:
    """Fixed fake LLM that records the history injected by the API layer."""

    def __init__(self) -> None:
        self.history_batches: list[list[dict[str, str]]] = []

    def generate(self, prompt: str, history: list[dict] | None = None, **kwargs: object) -> str:
        del prompt, kwargs
        self.history_batches.append(
            [{"role": item["role"], "content": item["content"]} for item in (history or [])],
        )
        batch = self.history_batches[-1]
        if len(batch) >= 4 and any("flash-attention-v1" in item["content"] for item in batch):
            return "沿用第 1 轮结论 flash-attention-v1 [S1]"
        return "已记录本轮结论 [S1]"


class ToolLoopLLM:
    """Two-step trajectory: the model picks a tool, then answers."""

    def __init__(self) -> None:
        self.calls = 0

    def generate_with_tools(self, prompt: str, **kwargs: object) -> dict:
        del prompt, kwargs
        self.calls += 1
        if self.calls == 1:
            return {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "search_knowledge",
                                "arguments": json.dumps({"query": "calibration"}),
                            },
                        },
                    ],
                },
            }
        return {"message": {"role": "assistant", "content": "Calibration is verified [S1]"}}


class ExplodingLLM:
    def generate(self, **kwargs: object) -> str:
        raise RuntimeError("model unavailable")


class FakeToolRegistry:
    async def execute(self, name: str, arguments: dict, **kwargs: object) -> dict:
        assert name == "search_knowledge"
        assert arguments == {"query": "calibration"}
        return {
            "results": [
                {
                    "id": "chunk-1",
                    "content": "Calibration is verified.",
                    "filename": "paper.pdf",
                    "locator": {"file_type": "pdf", "page_start": 2, "page_end": 2},
                    "score": 0.9,
                },
            ],
        }


class ChatHarness:
    """SQLite-backed API stack that can be restarted on the same database file."""

    def __init__(
        self,
        db_path: Path,
        llm: object | None = None,
        registry: object | None = None,
    ) -> None:
        self.db_path = db_path
        self.llm = llm or RecordingLLM()
        self.registry = registry
        self.engine = None
        self.sessions = None
        self.client: httpx.AsyncClient | None = None

    async def start(self) -> "ChatHarness":
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        app.dependency_overrides[get_db] = self._database
        app.dependency_overrides[get_orchestrator] = self._orchestrator
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )
        return self

    async def _database(self) -> AsyncIterator[SessionAdapter]:
        with self.sessions() as session:
            yield SessionAdapter(session)

    async def _orchestrator(self) -> AgentOrchestrator:
        return AgentOrchestrator(
            llm_client=self.llm,
            searcher=FakeSearcher(),
            hydrator=fake_hydrator,
            tool_registry=self.registry,
        )

    async def restart(self) -> "ChatHarness":
        """Drop the engine and connection pool, then reopen the same DB file."""
        await self.client.aclose()
        self.engine.dispose()
        return await self.start()

    async def stop(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        self.engine.dispose()


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[ChatHarness]:
    stack = await ChatHarness(tmp_path / "chat.db").start()
    yield stack
    await stack.stop()


@pytest.mark.asyncio
async def test_three_turn_conversation_injects_persisted_history(harness: ChatHarness) -> None:
    client = harness.client
    turns = [
        "第一轮结论：flash-attention-v1 作为基线",
        "第二轮：显存占用还有多少余量",
        "第三轮：沿用第一轮的结论继续",
    ]
    run_ids = []
    for message in turns:
        response = await client.post(
            "/api/v1/chat/message",
            json={
                "session_id": "unit-session-1",
                "project_ids": ["project-1"],
                "message": message,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["session_id"] == "unit-session-1"
        assert body["run_id"]
        assert body["run_status"] == "completed"
        assert body["citations"][0]["chunk_id"] == "chunk-1"
        run_ids.append(body["run_id"])

    llm = harness.llm
    assert llm.history_batches[0] == []
    assert llm.history_batches[1] == [
        {"role": "user", "content": turns[0]},
        {"role": "assistant", "content": "已记录本轮结论 [S1]"},
    ]
    assert llm.history_batches[2] == [
        {"role": "user", "content": turns[0]},
        {"role": "assistant", "content": "已记录本轮结论 [S1]"},
        {"role": "user", "content": turns[1]},
        {"role": "assistant", "content": "已记录本轮结论 [S1]"},
    ]

    third = await client.get("/api/v1/chat/sessions/unit-session-1")
    assert third.status_code == 200
    body = third.json()
    assert body["message_count"] == 6
    assert [item["content"] for item in body["messages"]] == [
        turns[0],
        "已记录本轮结论 [S1]",
        turns[1],
        "已记录本轮结论 [S1]",
        turns[2],
        "沿用第 1 轮结论 flash-attention-v1 [S1]",
    ]
    assert [item["role"] for item in body["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [item["turn_index"] for item in body["messages"]] == [0, 1, 2, 3, 4, 5]
    assert body["messages"][0]["run_id"] == run_ids[0]
    assert body["title"] == turns[0]

    with harness.sessions() as session:
        stored = list(
            session.execute(select(Conversation).order_by(Conversation.turn_index)).scalars(),
        )
        assert len(stored) == 6
        assert stored[1].message_metadata["citation_ids"] == ["S1"]
        assert stored[1].message_metadata["invalid_citation_ids"] == []


@pytest.mark.asyncio
async def test_history_survives_restart_and_pages_with_a_cursor(harness: ChatHarness) -> None:
    client = harness.client
    for index in range(3):
        response = await client.post(
            "/api/v1/chat/message",
            json={
                "session_id": "unit-session-2",
                "project_ids": [],
                "message": f"第 {index} 轮",
            },
        )
        assert response.status_code == 200, response.text

    await harness.restart()
    client = harness.client

    first_page = await client.get(
        "/api/v1/chat/sessions/unit-session-2",
        params={"limit": 2},
    )
    assert first_page.status_code == 200, first_page.text
    body = first_page.json()
    assert [item["content"] for item in body["messages"]] == ["第 2 轮", "已记录本轮结论 [S1]"]
    assert body["has_more"] is True
    assert body["next_cursor"] == "4"
    assert body["message_count"] == 6

    second_page = await client.get(
        "/api/v1/chat/sessions/unit-session-2",
        params={"limit": 2, "cursor": body["next_cursor"]},
    )
    assert [item["content"] for item in second_page.json()["messages"]] == [
        "第 1 轮",
        "已记录本轮结论 [S1]",
    ]
    assert second_page.json()["next_cursor"] == "2"

    third_page = await client.get(
        "/api/v1/chat/sessions/unit-session-2",
        params={"limit": 2, "cursor": second_page.json()["next_cursor"]},
    )
    assert [item["content"] for item in third_page.json()["messages"]] == [
        "第 0 轮",
        "已记录本轮结论 [S1]",
    ]
    assert third_page.json()["has_more"] is False
    assert third_page.json()["next_cursor"] is None

    invalid_cursor = await client.get(
        "/api/v1/chat/sessions/unit-session-2",
        params={"cursor": "not-a-number"},
    )
    assert invalid_cursor.status_code == 400

    missing = await client.get("/api/v1/chat/sessions/missing-session")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_tool_trace_is_persisted_and_readable_as_agent_steps(tmp_path: Path) -> None:
    stack = await ChatHarness(
        tmp_path / "tools.db",
        llm=ToolLoopLLM(),
        registry=FakeToolRegistry(),
    ).start()
    try:
        client = stack.client
        response = await client.post(
            "/api/v1/chat/message",
            json={
                "session_id": "unit-session-tools",
                "project_ids": ["project-1"],
                "message": "Where is calibration verified?",
            },
        )
        assert response.status_code == 200, response.text
        run_id = response.json()["run_id"]

        run_response = await client.get(f"/api/v1/chat/runs/{run_id}")
        assert run_response.status_code == 200, run_response.text
        run = run_response.json()
        assert run["status"] == "completed"
        assert run["run_type"] == "chat"
        assert run["goal"] == "Where is calibration verified?"
        assert run["step_count"] == 2
        assert [step["step_type"] for step in run["steps"]] == ["tool_call", "observation"]

        tool_call, observation = run["steps"]
        assert tool_call["tool_name"] == "search_knowledge"
        assert tool_call["arguments"] == {"query": "calibration"}
        assert observation["tool_name"] == "search_knowledge"
        assert "检索命中 1 段" in observation["result_summary"]
        assert "paper.pdf" in observation["result_summary"]
        assert len(observation["result_summary"]) <= 800
        assert observation["result_ref"] == "chunks:chunk-1"
        assert isinstance(observation["duration_ms"], int)
        assert observation["error"] is None

        with stack.sessions() as session:
            steps = list(
                session.execute(
                    select(AgentStep).order_by(AgentStep.step_index),
                ).scalars(),
            )
            assert [step.step_index for step in steps] == [0, 1]

        runs = await client.get("/api/v1/chat/sessions/unit-session-tools/runs")
        assert runs.status_code == 200, runs.text
        assert runs.json()["total"] == 1
        assert runs.json()["runs"][0]["run_id"] == run_id

        missing = await client.get("/api/v1/chat/runs/missing-run")
        assert missing.status_code == 404
    finally:
        await stack.stop()


@pytest.mark.asyncio
async def test_explicit_session_creation_auto_creation_and_listing(harness: ChatHarness) -> None:
    client = harness.client
    created = await client.post(
        "/api/v1/chat/sessions",
        json={"title": "论文精读", "project_ids": ["project-1"]},
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]
    assert created.json()["title"] == "论文精读"
    assert created.json()["status"] == "active"
    assert created.json()["message_count"] == 0

    auto = await client.post(
        "/api/v1/chat/message",
        json={
            "session_id": "auto-created-session",
            "project_ids": ["project-1"],
            "message": "自动创建会话并追问",
        },
    )
    assert auto.status_code == 200, auto.text
    assert auto.json()["session_id"] == "auto-created-session"

    listing = await client.get("/api/v1/chat/sessions")
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] == 2
    by_id = {item["session_id"]: item for item in body["sessions"]}
    assert by_id[session_id]["message_count"] == 0
    assert by_id["auto-created-session"]["message_count"] == 2
    assert by_id["auto-created-session"]["title"] == "自动创建会话并追问"

    runs = await client.get(f"/api/v1/chat/sessions/{session_id}/runs")
    assert runs.status_code == 200
    assert runs.json()["total"] == 0
    missing_runs = await client.get("/api/v1/chat/sessions/does-not-exist/runs")
    assert missing_runs.status_code == 404


@pytest.mark.asyncio
async def test_failed_turn_marks_the_run_failed_without_half_written_answer(
    tmp_path: Path,
) -> None:
    stack = await ChatHarness(tmp_path / "failure.db", llm=ExplodingLLM()).start()
    try:
        client = stack.client
        response = await client.post(
            "/api/v1/chat/message",
            json={
                "session_id": "unit-session-failure",
                "project_ids": ["project-1"],
                "message": "这会失败",
            },
        )
        assert response.status_code == 500

        with stack.sessions() as session:
            run = session.execute(select(AgentRun)).scalars().one()
            messages = list(
                session.execute(select(Conversation).order_by(Conversation.turn_index)).scalars(),
            )
        assert run.status == "failed"
        assert "model unavailable" in run.error
        assert run.finished_at is not None
        assert [message.role for message in messages] == ["user"]

        runs = await client.get("/api/v1/chat/sessions/unit-session-failure/runs")
        assert runs.status_code == 200
        assert runs.json()["runs"][0]["status"] == "failed"
        assert "model unavailable" in runs.json()["runs"][0]["error"]
    finally:
        await stack.stop()
