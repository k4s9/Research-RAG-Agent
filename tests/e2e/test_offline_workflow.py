from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import fitz
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.api.dependencies import get_ingest_pipeline, get_orchestrator, get_searcher
from src.config.settings import settings
from src.core.agent.orchestrator import AgentOrchestrator
from src.core.ingest.pipeline import DocumentIngestPipeline
from src.core.retrieval.embedder import Qwen3Embedder
from src.core.retrieval.hybrid_search import HybridSearch
from src.core.retrieval.hydration import hydrate_search_results
from src.db.models import Base
from src.db.postgres import get_db
from src.db.vector_store import InMemoryVectorStore
from src.main import app

pytestmark = pytest.mark.e2e


class CitationLLM:
    def generate(self, **kwargs) -> str:
        assert "[S1]" in kwargs["prompt"]
        return "The indexed evidence supports this answer [S1]."


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

    async def get(self, model: type, identifier: str):
        return self.session.get(model, identifier)

    async def execute(self, statement):
        return self.session.execute(statement)

    async def commit(self) -> None:
        self.session.commit()

    async def refresh(self, instance: object) -> None:
        self.session.refresh(instance)


@pytest.fixture
def offline_services(tmp_path: Path) -> Iterator[None]:
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

    orchestrator = AgentOrchestrator(
        llm_client=CitationLLM(),
        searcher=searcher,
        hydrator=hydrate,
    )

    async def pipeline_dependency() -> DocumentIngestPipeline:
        return pipeline

    async def searcher_dependency() -> HybridSearch:
        return searcher

    async def orchestrator_dependency() -> AgentOrchestrator:
        return orchestrator

    previous_upload_dir = settings.upload_dir
    settings.upload_dir = str(tmp_path / "uploads")
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_ingest_pipeline] = pipeline_dependency
    app.dependency_overrides[get_searcher] = searcher_dependency
    app.dependency_overrides[get_orchestrator] = orchestrator_dependency
    yield
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
