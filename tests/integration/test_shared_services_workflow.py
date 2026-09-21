"""Opt-in real model/PostgreSQL workflow, with an explicitly in-memory vector index.

Configure the application for remote models and TEST_POSTGRES_URL, then set
RUN_SHARED_SERVICES_E2E=1. Only a randomly named PostgreSQL schema is modified;
it is dropped in fixture teardown, including after an assertion failure.
"""

import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import fitz
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

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
from src.core.retrieval.reranker import Qwen3Reranker
from src.db.models import Base, Chunk, Document, Project
from src.db.postgres import get_db
from src.db.vector_store import InMemoryVectorStore
from src.main import app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_SHARED_SERVICES_E2E") != "1" or not os.getenv("TEST_POSTGRES_URL"),
        reason="requires RUN_SHARED_SERVICES_E2E=1, TEST_POSTGRES_URL and remote model settings",
    ),
]


@pytest.fixture
async def shared_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator:
    assert settings.embedding_provider == "remote"
    assert settings.reranker_provider == "remote"
    assert settings.llm_provider in {"openai", "deepseek"}
    schema = f"rag_probe_{uuid4().hex}"
    engine = create_async_engine(
        os.environ["TEST_POSTGRES_URL"],
        poolclass=NullPool,
        connect_args={"timeout": 10},
        execution_options={"schema_translate_map": {None: schema}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    old_overrides = dict(app.dependency_overrides)
    created = False
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.run_sync(Base.metadata.create_all)
        created = True

        async def database() -> AsyncIterator[AsyncSession]:
            async with sessions() as session:
                yield session

        async def hydrate(results: list[dict], project_ids: list[str]) -> list[dict]:
            return await hydrate_search_results(results, project_ids, db_session_factory=database)

        vectors = InMemoryVectorStore()
        embedder = Qwen3Embedder()
        searcher = HybridSearch(embedder=embedder, vector_store=vectors)
        registry = ResearchToolRegistry(searcher, hydrator=hydrate, db_session_factory=database)
        pipeline = DocumentIngestPipeline(
            embedder=embedder,
            vector_store=vectors,
            db_session_factory=database,
        )
        enricher = DocumentEnricher(db_session_factory=database)

        def orchestrator() -> AgentOrchestrator:
            # A fresh agent for every request must reload history from PostgreSQL.
            return AgentOrchestrator(searcher=searcher, hydrator=hydrate, tool_registry=registry)

        monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
        app.dependency_overrides.update(
            {
                get_db: database,
                get_ingest_pipeline: lambda: pipeline,
                get_searcher: lambda: searcher,
                get_document_enricher: lambda: enricher,
                get_orchestrator: orchestrator,
            },
        )
        yield SimpleNamespace(
            registry=registry,
            schema=schema,
            sessions=sessions,
            embedder=embedder,
            vectors=vectors,
            enricher=enricher,
        )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
        try:
            if created:
                async with engine.begin() as connection:
                    await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                async with engine.connect() as connection:
                    assert (
                        await connection.scalar(
                            text("SELECT count(*) FROM pg_namespace WHERE nspname = :schema"),
                            {"schema": schema},
                        )
                        == 0
                    )
        finally:
            await engine.dispose()


def pdf_bytes(content: str) -> bytes:
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), content)
        return document.tobytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("ingestion", ["upload", "seeded"])
async def test_shared_registry_search_and_multi_turn_chat(
    shared_stack: SimpleNamespace,
    ingestion: str,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        project = await client.post("/api/v1/projects", json={"name": "Shared service probe"})
        assert project.status_code == 201
        project_id = project.json()["id"]
        fixtures = [
            (
                "alpha.pdf",
                pdf_bytes(
                    "Alpha protocol uses verified calibration.\nIts calibration code is ALPHA-731.",
                ),
                "application/pdf",
            ),
            (
                "beta.pdf",
                pdf_bytes("Beta protocol measures timing.\nIts timing interval is 23 seconds."),
                "application/pdf",
            ),
            (
                "gamma.md",
                b"# Gamma\n\nGamma records the independent audit on Friday.\n",
                "text/markdown",
            ),
            (
                "delta.markdown",
                b"# Delta\n\nDelta keeps a stable locator for every result.\n",
                "text/markdown",
            ),
        ]
        documents = {}
        for filename, content, mime in fixtures:
            if ingestion == "seeded":
                # Exercise downstream services even when the upload path is broken.
                # This branch explicitly does not verify parsing or ingestion.
                if mime == "application/pdf":
                    with fitz.open(stream=content, filetype="pdf") as source:
                        body = source[0].get_text()
                    file_type = "pdf"
                    metadata = {"page_start": 1, "page_end": 1}
                else:
                    body = content.decode()
                    file_type = "markdown"
                    metadata = {"section_path": [Path(filename).stem], "start_line": 1}
                document_id, chunk_id = str(uuid4()), str(uuid4())
                async with shared_stack.sessions() as session:
                    project_row = await session.get(Project, project_id)
                    session.add(
                        Document(
                            id=document_id,
                            filename=filename,
                            file_type=file_type,
                            file_path="unused-synthetic-fixture",
                            status="ready",
                            title=Path(filename).stem,
                            doc_type="note",
                            projects=[project_row],
                            parse_metadata={"chunk_count": 1, "stage": "ready"},
                        ),
                    )
                    session.add(
                        Chunk(
                            id=chunk_id,
                            document_id=document_id,
                            chunk_index=0,
                            content=body,
                            content_type="text",
                            version_status="active",
                            chunk_metadata=metadata,
                        ),
                    )
                    await session.commit()
                vector = shared_stack.embedder.embed([body])[0]
                shared_stack.vectors.insert(
                    [
                        {
                            "id": chunk_id,
                            "entity_type": "chunk",
                            "content": body,
                            "dense_vector": vector,
                            "project_ids": [project_id],
                            "version_status": "active",
                            "content_type": "text",
                            "created_at": int(time.time()),
                        },
                    ],
                )
                await shared_stack.enricher.enrich_document(document_id)
                documents[filename] = document_id
                continue
            upload = await client.post(
                "/api/v1/documents/upload",
                files={"file": (filename, content, mime)},
                data={"project_ids": json.dumps([project_id]), "note": "synthetic live test"},
            )
            assert upload.status_code == 200, upload.text
            assert upload.json()["status"] == "ready"
            documents[filename] = upload.json()["document_id"]

        listing = await client.get("/api/v1/documents", params={"project_id": project_id})
        assert listing.status_code == 200
        rows = listing.json()["documents"]
        assert listing.json()["total"] == 4
        assert all(row["chunk_count"] > 0 for row in rows)
        enriched = sum(bool(row["summary"] and row["tags"]) for row in rows)
        print(
            json.dumps(
                {"probe": "documents", "ingestion": ingestion, "ready": 4, "enriched": enriched},
            ),
        )

        search = await client.post(
            "/api/v1/search",
            json={"query": "Alpha calibration code", "project_ids": [project_id], "top_k": 3},
        )
        assert search.status_code == 200, search.text
        results = search.json()["results"]
        assert results[0]["filename"] == "alpha.pdf"
        assert results[0]["locator"]["page_start"] == 1
        assert all(not row["retrieval"].get("retrieval_degraded") for row in results)
        print(json.dumps({"probe": "search", "scores": [row["score"] for row in results]}))
        outside = await client.post(
            "/api/v1/search",
            json={"query": "Alpha calibration code", "project_ids": [str(uuid4())]},
        )
        assert outside.status_code == 200
        assert outside.json()["results"] == []

        corrected = await client.patch(
            f"/api/v1/documents/{documents['alpha.pdf']}",
            json={"title": "Verified Alpha", "doc_type": "paper", "tags": ["live-verified"]},
        )
        assert corrected.status_code == 200
        filtered = await shared_stack.registry.execute(
            "search_knowledge",
            {"query": "Alpha calibration", "tags": ["live-verified"], "doc_types": ["paper"]},
            project_ids=[project_id],
            session_id="metadata-test",
        )
        assert filtered["results"]
        assert {row["filename"] for row in filtered["results"]} == {"alpha.pdf"}

        session_id = str(uuid4())
        run_ids = []
        for question in (
            "What is the calibration code of Alpha protocol? Search the project and cite [S1].",
            "Repeat that code and identify its protocol "
            "using the previous conversation and evidence.",
            "Use list_documents to list every uploaded filename in this project.",
        ):
            chat = await client.post(
                "/api/v1/chat/message",
                json={"session_id": session_id, "project_ids": [project_id], "message": question},
            )
            assert chat.status_code == 200, chat.text
            answer = chat.json()
            assert answer["run_status"] == "completed"
            assert answer["response"].strip()
            assert answer["invalid_citation_ids"] == []
            if len(run_ids) < 2:
                assert "ALPHA-731" in answer["response"]
                assert answer["citations"]
            else:
                assert all(filename in answer["response"] for filename in documents)
            run_ids.append(answer["run_id"])
            trace = await client.get(f"/api/v1/chat/runs/{answer['run_id']}")
            assert trace.status_code == 200
            assert trace.json()["status"] == "completed"
            print(
                json.dumps(
                    {
                        "probe": "chat",
                        "turn": len(run_ids),
                        "citations": len(answer["citations"]),
                        "steps": len(trace.json()["steps"]),
                        "tools": [item["tool"] for item in answer.get("tool_trace", [])],
                    },
                ),
            )

        history = await client.get(f"/api/v1/chat/sessions/{session_id}")
        assert history.status_code == 200
        assert history.json()["message_count"] == 6
        assert [row["turn_index"] for row in history.json()["messages"]] == list(range(6))
        runs = await client.get(f"/api/v1/chat/sessions/{session_id}/runs")
        assert runs.status_code == 200
        assert runs.json()["total"] == 3
        assert enriched == 4, "all four synthetic documents should receive real LLM metadata"


def test_shared_reranker_score_contract() -> None:
    """Catch a live endpoint's valid scores being silently discarded by retrieval."""
    documents = ["unrelated weather report", "verified calibration evidence"]

    class RecordingReranker(Qwen3Reranker):
        results: list[dict]

        def rerank(self, query: str, documents: list[str], top_k: int = 5) -> list[dict]:
            self.results = super().rerank(query, documents, top_k)
            return self.results

    reranker = RecordingReranker(provider="remote")
    searcher = HybridSearch(vector_store=InMemoryVectorStore(), reranker=reranker)
    degraded = []
    results = searcher._rerank(
        "verified calibration",
        [{"id": str(i), "content": doc} for i, doc in enumerate(documents)],
        top_k=2,
        degraded=degraded,
    )
    expected = [row.get("score", row.get("relevance_score")) for row in reranker.results]
    actual = [row["score"] for row in results]
    print(json.dumps({"probe": "reranker_scores", "expected": expected, "actual": actual}))
    assert all(isinstance(value, (float, int)) for value in expected)
    assert actual == pytest.approx(expected)
