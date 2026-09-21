import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace

import fitz
import httpx
import pytest
from sqlalchemy import Result, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import Executable

from src.api.dependencies import (
    get_document_enricher,
    get_ingest_pipeline,
    get_orchestrator,
    get_searcher,
)
from src.config.settings import settings
from src.core.agent.orchestrator import AgentOrchestrator
from src.core.agent.tools import ResearchToolRegistry
from src.core.ingest.enrichment import DocumentEnricher
from src.core.ingest.pipeline import DocumentIngestPipeline
from src.core.retrieval.embedder import Qwen3Embedder
from src.core.retrieval.hybrid_search import HybridSearch
from src.core.retrieval.hydration import hydrate_search_results
from src.db.models import AgentRun, Base, Conversation, Document
from src.db.postgres import get_db
from src.db.vector_store import InMemoryVectorStore
from src.main import app
from src.utils.async_llm_client import AsyncLLMClient

pytestmark = pytest.mark.e2e


class CitationLLM:
    def __init__(self) -> None:
        self.injected_history: list[list[dict[str, str]]] = []

    def generate(self, **kwargs: object) -> str:
        assert "[S1]" in kwargs["prompt"]
        self.injected_history.append(list(kwargs.get("history") or []))
        return "The indexed evidence supports this answer [S1]."


class DeterministicEnricher:
    """Offline stand-in for the LLM summary/tags job."""

    def __init__(self, sessions: sessionmaker) -> None:
        self.sessions = sessions

    async def enrich_document(self, document_id: str, **kwargs: object) -> dict:
        del kwargs
        with self.sessions() as session:
            document = session.get(Document, document_id)
            if document is None:
                return {}
            document.summary = f"离线摘要：{document.filename}"
            document.tags = ["offline", document.file_type]
            session.commit()
            return {"summary": document.summary, "tags": list(document.tags)}


def pdf_bytes(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    payload = document.tobytes()
    document.close()
    return payload


class AsyncSessionAdapter:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, instance: object) -> None:
        self.session.add(instance)

    def add_all(self, instances: list[object]) -> None:
        self.session.add_all(instances)

    async def get(self, model: type, identifier: str) -> object:
        return self.session.get(model, identifier)

    async def execute(self, statement: Executable) -> Result:
        return self.session.execute(statement)

    async def commit(self) -> None:
        self.session.commit()

    async def refresh(self, instance: object) -> None:
        self.session.refresh(instance)


@pytest.fixture
def offline_services(tmp_path: Path) -> Iterator[SimpleNamespace]:
    engine = create_engine(f"sqlite:///{tmp_path / 'e2e.db'}")
    sessions = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    async def database() -> AsyncIterator[AsyncSessionAdapter]:
        with sessions() as session:
            yield AsyncSessionAdapter(session)

    vector_store = InMemoryVectorStore()
    embedder = Qwen3Embedder(provider="local", dimension=64)
    pipeline = DocumentIngestPipeline(
        embedder=embedder,
        vector_store=vector_store,
        db_session_factory=database,
    )
    searcher = HybridSearch(embedder=embedder, vector_store=vector_store)

    async def hydrate(results: list[dict], project_ids: list[str]) -> list[dict]:
        return await hydrate_search_results(results, project_ids, db_session_factory=database)

    llm = CitationLLM()
    orchestrator = AgentOrchestrator(
        llm_client=llm,
        searcher=searcher,
        hydrator=hydrate,
        # 工具面必须绑定测试库：默认 get_db 指向真实数据库，离线跑不通
        tool_registry=ResearchToolRegistry(
            searcher=searcher,
            hydrator=hydrate,
            db_session_factory=database,
        ),
    )

    async def pipeline_dependency() -> DocumentIngestPipeline:
        return pipeline

    async def searcher_dependency() -> HybridSearch:
        return searcher

    async def orchestrator_dependency() -> AgentOrchestrator:
        return orchestrator

    enricher = DeterministicEnricher(sessions)

    async def enricher_dependency() -> DeterministicEnricher:
        return enricher

    previous_upload_dir = settings.upload_dir
    settings.upload_dir = str(tmp_path / "uploads")
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_ingest_pipeline] = pipeline_dependency
    app.dependency_overrides[get_searcher] = searcher_dependency
    app.dependency_overrides[get_orchestrator] = orchestrator_dependency
    app.dependency_overrides[get_document_enricher] = enricher_dependency
    yield SimpleNamespace(sessions=sessions, orchestrator=orchestrator, llm=llm)
    app.dependency_overrides.clear()
    settings.upload_dir = previous_upload_dir
    engine.dispose()


@pytest.mark.asyncio
async def test_four_document_upload_search_answer_and_citations(offline_services: None) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project_response = await client.post(
            "/api/v1/projects",
            json={"name": "Offline E2E", "description": "fixed corpus"},
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["id"]

        fixtures = [
            (
                "alpha.pdf",
                pdf_bytes("Alpha protocol uses verified calibration."),
                "application/pdf",
            ),
            (
                "beta.pdf",
                pdf_bytes("Beta protocol records reproducible timing."),
                "application/pdf",
            ),
            (
                "notes.md",
                b"# Evidence\n\nGamma result is independently verified.\n",
                "text/markdown",
            ),
            (
                "report.markdown",
                b"# Findings\n\nDelta result includes a stable locator.\n",
                "text/markdown",
            ),
        ]
        document_ids = []
        for filename, payload, mime_type in fixtures:
            response = await client.post(
                "/api/v1/documents/upload",
                files={"file": (filename, payload, mime_type)},
                data={"project_ids": f'["{project_id}"]'},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["status"] == "ready"
            document_ids.append(body["document_id"])

        for document_id in document_ids:
            status = await client.get(f"/api/v1/documents/{document_id}/status")
            assert status.status_code == 200
            assert status.json()["status"] == "ready"
            assert status.json()["chunk_count"] >= 1

        search = await client.post(
            "/api/v1/search",
            json={"query": "Alpha verified calibration", "project_ids": [project_id], "top_k": 3},
        )
        assert search.status_code == 200, search.text
        assert search.json()["results"]
        assert search.json()["results"][0]["filename"] == "alpha.pdf"
        assert search.json()["results"][0]["locator"]["page_start"] == 1

        chat = await client.post(
            "/api/v1/chat/message",
            json={
                "session_id": "e2e-session",
                "project_ids": [project_id],
                "message": "Alpha calibration?",
            },
        )
        assert chat.status_code == 200, chat.text
        assert chat.json()["citations"]
        assert chat.json()["citations"][0]["source_id"] == "S1"
        assert chat.json()["invalid_citation_ids"] == []


@pytest.mark.asyncio
async def test_three_turn_chat_keeps_history_and_run_trace(
    offline_services: SimpleNamespace,
) -> None:
    sessions = offline_services.sessions
    llm = offline_services.llm
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project = await client.post(
            "/api/v1/projects",
            json={"name": "Three Turn", "description": "multi-turn regression"},
        )
        assert project.status_code == 201
        project_id = project.json()["id"]
        upload = await client.post(
            "/api/v1/documents/upload",
            files={
                "file": (
                    "attention.md",
                    b"# Attention\n\nFlash Attention lowers memory usage.\n",
                    "text/markdown",
                ),
            },
            data={"project_ids": f'["{project_id}"]'},
        )
        assert upload.status_code == 200, upload.text

        session_id = "e2e-three-turn"
        questions = [
            "What attention implementation is used?",
            "What is the benefit?",
            "Summarize the first two turns.",
        ]
        run_ids = []
        for question in questions:
            response = await client.post(
                "/api/v1/chat/message",
                json={"session_id": session_id, "project_ids": [project_id], "message": question},
            )
            assert response.status_code == 200, response.text
            assert response.json()["session_id"] == session_id
            assert response.json()["run_id"]
            assert response.json()["run_status"] == "completed"
            run_ids.append(response.json()["run_id"])

        assert [len(batch) for batch in llm.injected_history] == [0, 2, 4]
        assert llm.injected_history[2][0]["content"] == questions[0]
        assert [item["role"] for item in llm.injected_history[2]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]

        history = await client.get(f"/api/v1/chat/sessions/{session_id}")
        assert history.status_code == 200, history.text
        body = history.json()
        assert body["message_count"] == 6
        assert [item["role"] for item in body["messages"]] == ["user", "assistant"] * 3
        assert [item["turn_index"] for item in body["messages"]] == [0, 1, 2, 3, 4, 5]
        assert body["messages"][0]["content"] == questions[0]
        assert body["messages"][4]["run_id"] == run_ids[2]

        with sessions() as session:
            stored_runs = list(
                session.execute(
                    select(AgentRun).where(AgentRun.session_id == session_id),
                ).scalars(),
            )
            stored_messages = list(
                session.execute(
                    select(Conversation)
                    .where(Conversation.session_id == session_id)
                    .order_by(Conversation.turn_index),
                ).scalars(),
            )
        assert len(stored_runs) == 3
        assert {run.status for run in stored_runs} == {"completed"}
        assert len(stored_messages) == 6

        run_response = await client.get(f"/api/v1/chat/runs/{run_ids[2]}")
        assert run_response.status_code == 200, run_response.text
        assert run_response.json()["status"] == "completed"
        assert run_response.json()["goal"] == questions[2]
        assert run_response.json()["steps"] == []

        runs = await client.get(f"/api/v1/chat/sessions/{session_id}/runs")
        assert runs.status_code == 200, runs.text
        assert runs.json()["total"] == 3
        assert {item["run_id"] for item in runs.json()["runs"]} == set(run_ids)


def tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


def tool_envelope(calls: list[dict], content: str | None = None) -> dict:
    return {"message": {"role": "assistant", "content": content, "tool_calls": calls}}


class MaterialInventoryLLM:
    """Deterministic tool-calling model for "我上传了哪些材料、分别讲什么？"."""

    def __init__(self, question: str) -> None:
        self.question = question
        self.step = 0
        self.list_results: list[dict] = []

    def generate_with_tools(self, prompt: str, **kwargs: object) -> dict:
        tool_messages = kwargs.get("tool_messages") or []
        self.step += 1
        if self.step == 1:
            return tool_envelope([tool_call("call-list", "list_documents", {})])
        if self.step == 2:
            payload = json.loads(tool_messages[-1]["content"])
            assert payload["result_count"] == len(payload["documents"]) >= 1
            self.list_results.append(payload)
            return tool_envelope(
                [
                    tool_call(
                        f"call-read-{document['document_id']}",
                        "read_document_range",
                        {"document_id": document["document_id"], "max_chars": 2000},
                    )
                    for document in payload["documents"]
                ],
            )
        if self.step == 3:
            return tool_envelope(
                [
                    tool_call(
                        "call-search",
                        "search_knowledge",
                        {"query": self.question, "top_k": 5},
                    ),
                ],
            )
        search_payload = json.loads(tool_messages[-1]["content"])
        sources = {
            item["filename"]: item.get("source_id", "") for item in search_payload["results"]
        }
        lines = ["已上传材料："]
        for document in self.list_results[-1]["documents"]:
            source_id = sources.get(document["filename"])
            marker = f" [{source_id}]" if source_id else ""
            lines.append(
                f"- {document['title']}（{document['filename']}，{document['doc_type']}）"
                f"标签: {'/'.join(document['tags'])}{marker}",
            )
        return tool_envelope([], content="\n".join(lines))


@pytest.mark.asyncio
async def test_material_inventory_lists_uploads_and_reflects_metadata_correction(
    offline_services: SimpleNamespace,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project = await client.post(
            "/api/v1/projects",
            json={"name": "Materials", "description": "文档注册表场景"},
        )
        assert project.status_code == 201
        project_id = project.json()["id"]

        uploads = [
            (
                "alpha.pdf",
                pdf_bytes("Attention Is All You Need\nAbstract: calibration verified."),
                "application/pdf",
            ),
            ("beta.pdf", pdf_bytes("Weekly Report\nProgress on retrieval."), "application/pdf"),
            ("notes.md", "# 实验笔记\n\n第一节：数据准备。\n".encode(), "text/markdown"),
        ]
        document_ids: dict[str, str] = {}
        for filename, payload, mime in uploads:
            upload = await client.post(
                "/api/v1/documents/upload",
                files={"file": (filename, payload, mime)},
                data={"project_ids": f'["{project_id}"]', "note": "batch-1"},
            )
            assert upload.status_code == 200, upload.text
            assert upload.json()["status"] == "ready"
            assert upload.json()["ingest_batch_id"]
            assert upload.json()["title"]
            document_ids[filename] = upload.json()["document_id"]

        listing = await client.get("/api/v1/documents", params={"project_id": project_id})
        assert listing.status_code == 200
        assert listing.json()["total"] == 3
        by_filename = {item["filename"]: item for item in listing.json()["documents"]}
        assert set(by_filename) == {"alpha.pdf", "beta.pdf", "notes.md"}
        assert by_filename["alpha.pdf"]["doc_type"] == "paper"
        assert by_filename["notes.md"]["doc_type"] == "note"
        for item in by_filename.values():
            assert item["summary"] == f"离线摘要：{item['filename']}"
            assert "offline" in item["tags"]

        question = "我上传了哪些材料、分别讲什么？"
        offline_services.orchestrator.llm_client = MaterialInventoryLLM(question)
        first = await client.post(
            "/api/v1/chat/message",
            json={
                "session_id": "e2e-materials",
                "project_ids": [project_id],
                "message": question,
            },
        )
        assert first.status_code == 200, first.text
        first_body = first.json()
        for filename in ("alpha.pdf", "beta.pdf", "notes.md"):
            assert filename in first_body["response"]
        assert "已上传材料" in first_body["response"]
        assert first_body["invalid_citation_ids"] == []
        assert {citation["filename"] for citation in first_body["citations"]} == {
            "alpha.pdf",
            "beta.pdf",
            "notes.md",
        }

        tools = [entry["tool"] for entry in first_body["tool_trace"]]
        assert tools[0] == "list_documents"
        assert tools.count("read_document_range") == 3
        assert "search_knowledge" in tools
        listed = first_body["tool_trace"][0]["result"]
        assert listed["result_count"] == 3
        assert {item["doc_type"] for item in listed["documents"]} == {"paper", "report", "note"}
        for entry in first_body["tool_trace"][1:4]:
            chunk_indices = [chunk["chunk_index"] for chunk in entry["result"]["chunks"]]
            expected = list(range(chunk_indices[0], chunk_indices[0] + len(chunk_indices)))
            assert chunk_indices == expected

        correction = await client.patch(
            f"/api/v1/documents/{document_ids['alpha.pdf']}",
            json={"title": "Attention Is All You Need (v2)", "tags": ["corrected"]},
        )
        assert correction.status_code == 200
        assert correction.json()["title"] == "Attention Is All You Need (v2)"
        filtered = await offline_services.orchestrator.tool_registry.execute(
            "search_knowledge",
            {"query": "calibration", "tags": ["corrected"]},
            project_ids=[project_id],
            session_id="e2e-materials",
        )
        assert filtered["results"]
        assert {item["document_id"] for item in filtered["results"]} == {document_ids["alpha.pdf"]}
        assert all(item["tags"] == ["corrected"] for item in filtered["results"])

        offline_services.orchestrator.llm_client = MaterialInventoryLLM(question)
        second = await client.post(
            "/api/v1/chat/message",
            json={
                "session_id": "e2e-materials",
                "project_ids": [project_id],
                "message": question,
            },
        )
        assert second.status_code == 200, second.text
        second_body = second.json()
        assert "Attention Is All You Need (v2)" in second_body["response"]
        assert "标签: corrected" in second_body["response"]
        corrected = second_body["tool_trace"][0]["result"]["documents"]
        assert next(item for item in corrected if item["filename"] == "alpha.pdf")["tags"] == [
            "corrected",
        ]


@pytest.mark.asyncio
async def test_upload_enrichment_leaves_registry_responsive(
    offline_services: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(settings, "llm_provider", "openai")

    async def model_response(request: httpx.Request) -> httpx.Response:
        del request
        entered.set()
        await release.wait()
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "Verified asynchronous enrichment",
                                    "tags": ["model"],
                                },
                            ),
                        },
                    },
                ],
            },
        )

    async def database() -> AsyncIterator[AsyncSessionAdapter]:
        with offline_services.sessions() as session:
            yield AsyncSessionAdapter(session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(model_response)) as model_client:
        enricher = DocumentEnricher(AsyncLLMClient(model_client), db_session_factory=database)

        async def enrichment_dependency() -> DocumentEnricher:
            return enricher

        app.dependency_overrides[get_document_enricher] = enrichment_dependency
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            project = await client.post("/api/v1/projects", json={"name": "async enrichment"})
            project_id = project.json()["id"]
            upload = asyncio.create_task(
                client.post(
                    "/api/v1/documents/upload",
                    files={
                        "file": ("async.md", b"# Evidence\n\nVerified evidence.", "text/markdown"),
                    },
                    data={"project_ids": json.dumps([project_id])},
                ),
            )
            try:
                await asyncio.wait_for(entered.wait(), 3)
                listing = await asyncio.wait_for(
                    client.get("/api/v1/documents", params={"project_id": project_id}),
                    1,
                )
                document = listing.json()["documents"][0]
                assert document["status"] == "ready"
                assert document["summary"] is None
                correction = await client.patch(
                    f"/api/v1/documents/{document['id']}",
                    json={"tags": ["human"]},
                )
                assert correction.status_code == 200
                assert not upload.done()
            finally:
                release.set()
                response = await upload
            assert response.status_code == 200, response.text
            detail = await client.get(f"/api/v1/documents/{document['id']}")
            assert detail.json()["summary"] == "Verified asynchronous enrichment"
            assert detail.json()["tags"] == ["human"]
