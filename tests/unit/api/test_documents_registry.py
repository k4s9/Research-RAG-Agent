from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Chunk, Document, IngestBatch, Project
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


def outline_entry(
    title: str,
    level: int,
    *,
    page: int | None = None,
    section_path: list[str] | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
) -> dict:
    """Expected wire shape: the schema serializes every optional field."""
    return {
        "title": title,
        "level": level,
        "page": page,
        "section_path": section_path or [],
        "line_start": line_start,
        "line_end": line_end,
    }


OUTLINE = [
    outline_entry("Attention Is All You Need", 1, page=1),
    outline_entry("1 Introduction", 2, page=1),
]


def seed(factory: sessionmaker) -> None:
    with factory() as session:
        session.add_all(
            [Project(id="project-1", name="Alpha"), Project(id="project-2", name="Beta")],
        )
        session.add(
            IngestBatch(
                id="batch-1",
                session_id=None,
                project_ids=["project-1"],
                note="第一次文献上传",
                file_count=2,
            ),
        )
        session.add_all(
            [
                Document(
                    id="doc-paper",
                    filename="1706.03762.pdf",
                    file_type="pdf",
                    file_path="/tmp/1706.03762.pdf",
                    status="ready",
                    title="Attention Is All You Need",
                    doc_type="paper",
                    authors=["Ashish Vaswani", "Noam Shazeer"],
                    year=2017,
                    venue="arXiv",
                    tags=["attention", "transformer"],
                    summary="Transformer 架构论文",
                    page_count=2,
                    ingest_batch_id="batch-1",
                    parse_metadata={"outline": OUTLINE},
                    extra_metadata={"source": "pdf_heuristic"},
                    created_at=datetime(2026, 1, 1),
                ),
                Document(
                    id="doc-report",
                    filename="weekly-2026.pdf",
                    file_type="pdf",
                    file_path="/tmp/weekly-2026.pdf",
                    status="processing",
                    title="Weekly Report",
                    doc_type="report",
                    year=2026,
                    tags=["周报"],
                    created_at=datetime(2026, 2, 1),
                ),
                Document(
                    id="doc-note",
                    filename="notes.md",
                    file_type="markdown",
                    file_path="/tmp/notes.md",
                    status="ready",
                    title="实验笔记",
                    doc_type="note",
                    year=2025,
                    tags=["笔记"],
                    created_at=datetime(2026, 3, 1),
                ),
            ],
        )
        project_alpha = session.get(Project, "project-1")
        project_beta = session.get(Project, "project-2")
        project_alpha.documents.extend(
            [session.get(Document, "doc-paper"), session.get(Document, "doc-report")],
        )
        project_beta.documents.append(session.get(Document, "doc-note"))
        session.add_all(
            [
                Chunk(
                    id="paper-c0",
                    document_id="doc-paper",
                    chunk_index=0,
                    content="Attention 是论文的核心机制。",
                    content_type="text",
                    chunk_metadata={"page_start": 1, "page_end": 1},
                ),
                Chunk(
                    id="paper-c1",
                    document_id="doc-paper",
                    chunk_index=1,
                    content="Table 1 对比了不同模型。",
                    content_type="table",
                    chunk_metadata={"page_start": 2, "page_end": 2},
                ),
                Chunk(
                    id="paper-c2",
                    document_id="doc-paper",
                    chunk_index=2,
                    content="结论：并行化带来吞吐提升。",
                    content_type="text",
                    chunk_metadata={"page_start": 2, "page_end": 2},
                ),
                Chunk(
                    id="note-c0",
                    document_id="doc-note",
                    chunk_index=0,
                    content="第一节：数据准备。",
                    content_type="text",
                    chunk_metadata={
                        "section_path": ["方法", "数据准备"],
                        "heading_level": 2,
                        "section_start_line": 1,
                        "section_end_line": 5,
                    },
                ),
            ],
        )
        session.commit()


class RegistryHarness:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.engine = None
        self.sessions = None
        self.client: httpx.AsyncClient | None = None

    async def start(self) -> "RegistryHarness":
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        seed(self.sessions)
        app.dependency_overrides[get_db] = self._database
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )
        return self

    async def _database(self) -> AsyncIterator[SessionAdapter]:
        with self.sessions() as session:
            yield SessionAdapter(session)

    async def stop(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        self.engine.dispose()


@pytest.fixture
async def registry(tmp_path: Path) -> AsyncIterator[RegistryHarness]:
    harness = await RegistryHarness(tmp_path / "registry.db").start()
    yield harness
    await harness.stop()


def ids(payload: dict) -> list[str]:
    return [item["id"] for item in payload["documents"]]


@pytest.mark.asyncio
async def test_document_list_filters(registry: RegistryHarness) -> None:
    client = registry.client

    response = await client.get("/api/v1/documents")
    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert ids(response.json()) == ["doc-note", "doc-report", "doc-paper"]

    by_project = await client.get("/api/v1/documents", params={"project_id": "project-1"})
    assert ids(by_project.json()) == ["doc-report", "doc-paper"]

    by_type = await client.get("/api/v1/documents", params={"doc_type": "paper"})
    assert ids(by_type.json()) == ["doc-paper"]

    by_tag = await client.get("/api/v1/documents", params={"tag": "笔记"})
    assert ids(by_tag.json()) == ["doc-note"]

    by_year = await client.get("/api/v1/documents", params={"year": 2017})
    assert ids(by_year.json()) == ["doc-paper"]

    by_status = await client.get("/api/v1/documents", params={"status": "processing"})
    assert ids(by_status.json()) == ["doc-report"]

    combined = await client.get(
        "/api/v1/documents",
        params={"project_id": "project-1", "doc_type": "paper", "year": 2017},
    )
    assert ids(combined.json()) == ["doc-paper"]
    assert combined.json()["documents"][0]["chunk_count"] == 3
    assert combined.json()["documents"][0]["tags"] == ["attention", "transformer"]

    empty = await client.get("/api/v1/documents", params={"tag": "does-not-exist"})
    assert empty.json() == {"documents": [], "total": 0}

    invalid = await client.get("/api/v1/documents", params={"doc_type": "blog"})
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_document_list_pagination(registry: RegistryHarness) -> None:
    client = registry.client

    first = await client.get("/api/v1/documents", params={"limit": 1, "offset": 0})
    second = await client.get("/api/v1/documents", params={"limit": 1, "offset": 1})
    last = await client.get("/api/v1/documents", params={"limit": 1, "offset": 2})

    assert first.json()["total"] == 3
    assert second.json()["total"] == 3
    assert last.json()["total"] == 3
    pages = [ids(first.json()), ids(second.json()), ids(last.json())]
    assert pages == [["doc-note"], ["doc-report"], ["doc-paper"]]

    out_of_range = await client.get("/api/v1/documents", params={"limit": 1, "offset": 9})
    assert out_of_range.json() == {"documents": [], "total": 3}
    assert (await client.get("/api/v1/documents", params={"limit": 0})).status_code == 422
    assert (await client.get("/api/v1/documents", params={"limit": 500})).status_code == 422


@pytest.mark.asyncio
async def test_document_detail_returns_outline_and_batch(registry: RegistryHarness) -> None:
    client = registry.client

    response = await client.get("/api/v1/documents/doc-paper")
    assert response.status_code == 200
    payload = response.json()

    assert payload["title"] == "Attention Is All You Need"
    assert payload["doc_type"] == "paper"
    assert payload["year"] == 2017
    assert payload["venue"] == "arXiv"
    assert payload["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert payload["summary"] == "Transformer 架构论文"
    assert payload["page_count"] == 2
    assert payload["chunk_count"] == 3
    assert payload["project_ids"] == ["project-1"]
    assert payload["outline"] == OUTLINE
    assert payload["content_type_counts"] == {"text": 2, "table": 1}
    assert payload["ingest_batch"] == {
        "id": "batch-1",
        "note": "第一次文献上传",
        "file_count": 2,
        "project_ids": ["project-1"],
        "created_at": payload["ingest_batch"]["created_at"],
    }

    markdown = await client.get("/api/v1/documents/doc-note")
    assert markdown.json()["outline"] == [
        outline_entry(
            "数据准备",
            2,
            section_path=["方法", "数据准备"],
            line_start=1,
            line_end=5,
        ),
    ]
    assert markdown.json()["ingest_batch"] is None

    missing = await client.get("/api/v1/documents/doc-missing")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_patch_document_corrects_metadata(registry: RegistryHarness) -> None:
    client = registry.client

    response = await client.patch(
        "/api/v1/documents/doc-paper",
        json={
            "title": "  Attention   Is All You Need (v2) ",
            "doc_type": "report",
            "tags": ["attention", "attention", "  transformer  ", "x" * 60],
            "authors": ["Ashish Vaswani", "Ashish Vaswani", "Noam Shazeer"],
            "year": 2018,
            "venue": " NeurIPS ",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Attention Is All You Need (v2)"
    assert payload["doc_type"] == "report"
    assert payload["year"] == 2018
    assert payload["venue"] == "NeurIPS"
    assert payload["tags"] == ["attention", "transformer", "x" * 40]
    assert payload["authors"] == ["Ashish Vaswani", "Noam Shazeer"]

    detail = await client.get("/api/v1/documents/doc-paper")
    assert detail.json()["title"] == "Attention Is All You Need (v2)"
    assert detail.json()["tags"] == ["attention", "transformer", "x" * 40]

    # 订正后按新标签/新类型可检索
    by_tag = await client.get("/api/v1/documents", params={"tag": "transformer"})
    assert ids(by_tag.json()) == ["doc-paper"]
    by_type = await client.get("/api/v1/documents", params={"doc_type": "report"})
    assert ids(by_type.json()) == ["doc-report", "doc-paper"]

    # 部分字段更新不动其他字段
    partial = await client.patch("/api/v1/documents/doc-note", json={"year": 2024})
    assert partial.json()["year"] == 2024
    assert partial.json()["title"] == "实验笔记"
    assert partial.json()["tags"] == ["笔记"]

    bad_type = await client.patch("/api/v1/documents/doc-note", json={"doc_type": "blog"})
    bad_year = await client.patch("/api/v1/documents/doc-note", json={"year": 1800})
    missing = await client.patch("/api/v1/documents/doc-missing", json={"year": 2024})
    assert (bad_type.status_code, bad_year.status_code, missing.status_code) == (422, 422, 404)
