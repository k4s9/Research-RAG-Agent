from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.core.agent.tools import ResearchToolRegistry, ToolExecutionError
from src.core.retrieval.hybrid_search import HybridSearch
from src.core.retrieval.hydration import hydrate_search_results
from src.db.models import Base, Chunk, Document, Project
from src.db.vector_store import InMemoryVectorStore

pytestmark = pytest.mark.unit

OUTLINE = [
    {"title": "Attention Is All You Need", "page": 1, "level": 1},
    {"title": "1 Introduction", "page": 1, "level": 2},
    {"title": "2 Background", "page": 2, "level": 2},
]


class SessionAdapter:
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


class RecordingSearcher:
    """Returns scored hits in retrieval order (not document order)."""

    def __init__(self, results: list[dict]) -> None:
        self.results = results
        self.calls: list[dict] = []

    def search(self, **kwargs: object) -> list[dict]:
        self.calls.append(kwargs)
        results = self.results
        if "chunk_ids" in kwargs:
            results = [item for item in results if item["id"] in kwargs["chunk_ids"]]
        return list(results[: kwargs.get("top_k", 5)])


def seed(factory: sessionmaker) -> None:
    with factory() as session:
        session.add_all(
            [Project(id="project-1", name="Alpha"), Project(id="project-2", name="Beta")],
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
                    year=2017,
                    tags=["attention"],
                    page_count=3,
                    ingest_batch_id="batch-1",
                    parse_metadata={"outline": OUTLINE},
                    created_at=datetime(2026, 1, 1),
                ),
                Document(
                    id="doc-report",
                    filename="weekly.pdf",
                    file_type="pdf",
                    file_path="/tmp/weekly.pdf",
                    status="ready",
                    title="Weekly Report",
                    doc_type="report",
                    year=2026,
                    tags=["周报"],
                    ingest_batch_id="batch-1",
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
        session.add_all(
            [
                Chunk(
                    id="paper-c0",
                    document_id="doc-paper",
                    chunk_index=0,
                    content="第一页正文：摘要。" + "补充说明" * 50,
                    content_type="text",
                    chunk_metadata={"page_start": 1, "page_end": 1},
                ),
                Chunk(
                    id="paper-c1",
                    document_id="doc-paper",
                    chunk_index=1,
                    content="第二页正文 A。",
                    content_type="text",
                    chunk_metadata={"page_start": 2, "page_end": 2},
                ),
                Chunk(
                    id="paper-c2",
                    document_id="doc-paper",
                    chunk_index=2,
                    content="第二页正文 B。",
                    content_type="text",
                    chunk_metadata={"page_start": 2, "page_end": 2},
                ),
                Chunk(
                    id="paper-c3",
                    document_id="doc-paper",
                    chunk_index=3,
                    content="第三页结论。",
                    content_type="text",
                    chunk_metadata={"page_start": 3, "page_end": 3},
                ),
                Chunk(
                    id="report-c0",
                    document_id="doc-report",
                    chunk_index=0,
                    content="Weekly retrieval progress.",
                    content_type="text",
                    chunk_metadata={"page_start": 1},
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
                        "section_end_line": 4,
                    },
                ),
                Chunk(
                    id="note-c1",
                    document_id="doc-note",
                    chunk_index=1,
                    content="第二节：模型训练。",
                    content_type="text",
                    chunk_metadata={
                        "section_path": ["方法", "模型训练"],
                        "heading_level": 2,
                        "section_start_line": 5,
                        "section_end_line": 9,
                    },
                ),
            ],
        )
        session.commit()
        project_alpha = session.get(Project, "project-1")
        project_beta = session.get(Project, "project-2")
        project_alpha.documents.extend(
            [session.get(Document, "doc-paper"), session.get(Document, "doc-report")],
        )
        project_beta.documents.append(session.get(Document, "doc-note"))
        session.commit()


@pytest.fixture
def sessions(tmp_path: Path) -> sessionmaker:
    engine = create_engine(f"sqlite:///{tmp_path / 'tools.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    seed(factory)
    yield factory
    engine.dispose()


def database(factory: sessionmaker) -> Callable[[], AsyncIterator[SessionAdapter]]:
    async def provider() -> AsyncIterator[SessionAdapter]:
        with factory() as session:
            yield SessionAdapter(session)

    return provider


def registry(
    factory: sessionmaker,
    searcher: RecordingSearcher | None = None,
    hydrator: object | None = None,
) -> ResearchToolRegistry:
    return ResearchToolRegistry(
        searcher=searcher or RecordingSearcher([]),
        hydrator=hydrator or (lambda results, project_ids: _passthrough(results)),
        db_session_factory=database(factory),
    )


async def _passthrough(results: list[dict]) -> list[dict]:
    return list(results)


@pytest.mark.asyncio
async def test_list_documents_filters_and_scope(sessions: sessionmaker) -> None:
    tools = registry(sessions)

    everything = await tools.execute(
        "list_documents",
        {},
        project_ids=["project-1", "project-2"],
        session_id="session-1",
    )
    assert [item["document_id"] for item in everything["documents"]] == [
        "doc-note",
        "doc-report",
        "doc-paper",
    ]
    assert everything["total_matched"] == 3
    paper = everything["documents"][2]
    assert paper["doc_type"] == "paper"
    assert paper["year"] == 2017
    assert paper["tags"] == ["attention"]

    papers_only = await tools.execute(
        "list_documents",
        {"doc_type": "paper"},
        project_ids=["project-1", "project-2"],
        session_id="session-1",
    )
    assert [item["document_id"] for item in papers_only["documents"]] == ["doc-paper"]
    assert papers_only["result_count"] == 1

    scoped = await tools.execute(
        "list_documents",
        {},
        project_ids=["project-2"],
        session_id="session-1",
    )
    assert [item["document_id"] for item in scoped["documents"]] == ["doc-note"]

    by_tag = await tools.execute(
        "list_documents",
        {"tag": "周报"},
        project_ids=["project-1", "project-2"],
        session_id="session-1",
    )
    assert [item["document_id"] for item in by_tag["documents"]] == ["doc-report"]

    by_year = await tools.execute(
        "list_documents",
        {"year": 2025},
        project_ids=["project-1", "project-2"],
        session_id="session-1",
    )
    assert [item["document_id"] for item in by_year["documents"]] == ["doc-note"]

    limited = await tools.execute(
        "list_documents",
        {"limit": 1},
        project_ids=["project-1", "project-2"],
        session_id="session-1",
    )
    assert len(limited["documents"]) == 1
    assert limited["total_matched"] == 3

    # §9.1「我上周插入的那批材料是什么？」
    batch = await tools.execute(
        "list_documents",
        {"ingest_batch_id": "batch-1"},
        project_ids=["project-1", "project-2"],
        session_id="session-1",
    )
    assert [item["document_id"] for item in batch["documents"]] == ["doc-report", "doc-paper"]
    assert {item["ingest_batch_id"] for item in batch["documents"]} == {"batch-1"}
    assert batch["documents"][-1]["ingest_batch_id"] == "batch-1"

    with pytest.raises(ToolExecutionError, match="exceeds"):
        await tools.execute(
            "list_documents",
            {"project_ids": ["project-2"]},
            project_ids=["project-1"],
            session_id="session-1",
        )
    with pytest.raises(ToolExecutionError, match="doc_type"):
        await tools.execute(
            "list_documents",
            {"doc_type": "blog"},
            project_ids=["project-1"],
            session_id="session-1",
        )
    with pytest.raises(ToolExecutionError, match="ingest_batch_id"):
        await tools.execute(
            "list_documents",
            {"ingest_batch_id": "  "},
            project_ids=["project-1"],
            session_id="session-1",
        )
    with pytest.raises(ToolExecutionError, match="unknown tool arguments"):
        await tools.execute(
            "list_documents",
            {"documents": []},
            project_ids=["project-1"],
            session_id="session-1",
        )


@pytest.mark.asyncio
async def test_get_document_outline_respects_project_scope(sessions: sessionmaker) -> None:
    tools = registry(sessions)

    result = await tools.execute(
        "get_document_outline",
        {"document_id": "doc-paper"},
        project_ids=["project-1"],
        session_id="session-1",
    )
    assert result["document_id"] == "doc-paper"
    assert result["outline"] == OUTLINE
    assert result["chunk_count"] == 4

    markdown = await tools.execute(
        "get_document_outline",
        {"document_id": "doc-note"},
        project_ids=["project-2"],
        session_id="session-1",
    )
    assert [entry["title"] for entry in markdown["outline"]] == ["数据准备", "模型训练"]

    with pytest.raises(ToolExecutionError, match="not found"):
        await tools.execute(
            "get_document_outline",
            {"document_id": "doc-note"},
            project_ids=["project-1"],
            session_id="session-1",
        )
    with pytest.raises(ToolExecutionError, match="document_id"):
        await tools.execute(
            "get_document_outline",
            {"document_id": ""},
            project_ids=["project-1"],
            session_id="session-1",
        )


@pytest.mark.asyncio
async def test_read_document_range_is_contiguous_document_order(sessions: sessionmaker) -> None:
    tools = registry(sessions)

    page_two = await tools.execute(
        "read_document_range",
        {"document_id": "doc-paper", "page_start": 2, "page_end": 2},
        project_ids=["project-1"],
        session_id="session-1",
    )
    assert [chunk["chunk_id"] for chunk in page_two["chunks"]] == ["paper-c1", "paper-c2"]
    assert [chunk["chunk_index"] for chunk in page_two["chunks"]] == [1, 2]
    assert page_two["content"] == "第二页正文 A。\n\n第二页正文 B。"
    assert page_two["truncated"] is False

    tail = await tools.execute(
        "read_document_range",
        {"document_id": "doc-paper", "page_start": 2},
        project_ids=["project-1"],
        session_id="session-1",
    )
    indices = [chunk["chunk_index"] for chunk in tail["chunks"]]
    assert indices == list(range(indices[0], indices[0] + len(indices)))
    assert indices == [1, 2, 3]

    section = await tools.execute(
        "read_document_range",
        {"document_id": "doc-note", "section_path": ["方法", "模型训练"]},
        project_ids=["project-2"],
        session_id="session-1",
    )
    assert [chunk["chunk_id"] for chunk in section["chunks"]] == ["note-c1"]
    assert section["content"] == "第二节：模型训练。"

    # 第一节 chunk 超过预算：截断发生在文档顺序的边界上，且不会跳页拼接
    truncated = await tools.execute(
        "read_document_range",
        {"document_id": "doc-paper", "max_chars": 200},
        project_ids=["project-1"],
        session_id="session-1",
    )
    assert truncated["truncated"] is True
    assert [chunk["chunk_index"] for chunk in truncated["chunks"]] == [0]
    assert len(truncated["content"]) == 200
    assert truncated["next_cursor"] == {"start_chunk_index": 0, "start_char": 200}
    continuation = await tools.execute(
        "read_document_range",
        {"document_id": "doc-paper", "max_chars": 200, **truncated["next_cursor"]},
        project_ids=["project-1"],
        session_id="session-1",
    )
    assert continuation["chunks"][0]["char_start"] == 200
    with sessions() as session:
        original = session.get(Chunk, "paper-c0").content
        assert truncated["chunks"][0]["content"] + continuation["chunks"][0]["content"] == original


@pytest.mark.asyncio
async def test_read_document_range_validation_and_scope(sessions: sessionmaker) -> None:
    tools = registry(sessions)

    with pytest.raises(ToolExecutionError, match="not found"):
        await tools.execute(
            "read_document_range",
            {"document_id": "doc-paper"},
            project_ids=["project-2"],
            session_id="session-1",
        )
    with pytest.raises(ToolExecutionError, match="page_end"):
        await tools.execute(
            "read_document_range",
            {"document_id": "doc-paper", "page_start": 3, "page_end": 1},
            project_ids=["project-1"],
            session_id="session-1",
        )
    with pytest.raises(ToolExecutionError, match="max_chars"):
        await tools.execute(
            "read_document_range",
            {"document_id": "doc-paper", "max_chars": 10},
            project_ids=["project-1"],
            session_id="session-1",
        )
    with pytest.raises(ToolExecutionError, match="no document content"):
        await tools.execute(
            "read_document_range",
            {"document_id": "doc-paper", "page_start": 9},
            project_ids=["project-1"],
            session_id="session-1",
        )


@pytest.mark.asyncio
async def test_search_knowledge_filters_by_doc_type_and_tag(sessions: sessionmaker) -> None:
    hits = [
        {"id": "paper-c0", "document_id": "doc-paper", "score": 0.9},
        {"id": "report-c0", "document_id": "doc-report", "score": 0.8},
        {"id": "note-c0", "document_id": "doc-note", "score": 0.7},
        {"id": "paper-c1", "document_id": "doc-paper", "score": 0.6},
    ]
    hydrated = [
        {
            **hit,
            "doc_type": doc_type,
            "tags": tags,
            "content": f"内容 {hit['id']}",
            "filename": f"{hit['document_id']}.pdf",
        }
        for hit, doc_type, tags in zip(
            hits,
            ["paper", "report", "note", "paper"],
            [["attention"], ["周报"], ["笔记"], ["attention"]],
            strict=True,
        )
    ]

    async def hydrator(results: list[dict], project_ids: list[str]) -> list[dict]:
        del project_ids
        return [item for item in hydrated if item["id"] in {hit["id"] for hit in results}]

    searcher = RecordingSearcher(hits)
    tools = registry(sessions, searcher=searcher, hydrator=hydrator)

    papers = await tools.execute(
        "search_knowledge",
        {"query": "attention", "top_k": 2, "doc_types": ["paper"]},
        project_ids=["project-1", "project-2"],
        session_id="session-1",
    )
    assert [item["id"] for item in papers["results"]] == ["paper-c0", "paper-c1"]
    assert {item["doc_type"] for item in papers["results"]} == {"paper"}
    assert [item["source_id"] for item in papers["results"]] == ["S1", "S2"]
    # Scope must be applied before Top-K, not patched by overfetching.
    assert searcher.calls[-1]["top_k"] == 2
    assert set(searcher.calls[-1]["chunk_ids"]) == {f"paper-c{i}" for i in range(4)}

    tagged = await tools.execute(
        "search_knowledge",
        {"query": "周报", "top_k": 3, "tags": ["周报"]},
        project_ids=["project-1", "project-2"],
        session_id="session-1",
    )
    assert [item["id"] for item in tagged["results"]] == ["report-c0"]

    unfiltered = await tools.execute(
        "search_knowledge",
        {"query": "attention", "top_k": 2},
        project_ids=["project-1", "project-2"],
        session_id="session-1",
    )
    assert len(unfiltered["results"]) == 2

    with pytest.raises(ToolExecutionError, match="doc_types"):
        await tools.execute(
            "search_knowledge",
            {"query": "attention", "doc_types": "paper"},
            project_ids=["project-1"],
            session_id="session-1",
        )


@pytest.mark.asyncio
async def test_list_multi_project_document_once_without_json_distinct(
    sessions: sessionmaker,
) -> None:
    with sessions() as session:
        project = session.get(Project, "project-2")
        project.documents.append(session.get(Document, "doc-paper"))
        session.commit()
    statements = []

    def record(connection: object, cursor: object, statement: str, *args: object) -> None:
        del connection, cursor, args
        statements.append(statement)

    engine = sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        result = await registry(sessions).execute(
            "list_documents",
            {},
            project_ids=["project-1", "project-2"],
            session_id="s",
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(result["documents"]) == 3
    assert [item["id"] for item in result["documents"]].count("doc-paper") == 1
    assert any("EXISTS" in statement for statement in statements)
    assert not any("SELECT DISTINCT" in statement for statement in statements)


class ConstantEmbedder:
    provider = "local"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


@pytest.mark.asyncio
async def test_metadata_filter_precedes_both_retrieval_channels_and_tracks_corrections(
    sessions: sessionmaker,
) -> None:
    store = InMemoryVectorStore()
    entities = []
    with sessions() as session:
        for i in range(80):
            identifier = f"distractor-{i}"
            session.add(
                Chunk(
                    id=identifier,
                    document_id="doc-report",
                    chunk_index=i + 1,
                    content="attention",
                    content_type="text",
                    chunk_metadata={"page_start": 1},
                ),
            )
            entities.append(
                {
                    "id": identifier,
                    "dense_vector": [1.0, 0.0],
                    "content": "attention",
                    "project_ids": ["project-1"],
                    "version_status": "active",
                    "content_type": "text",
                },
            )
        session.commit()
    entities.append(
        {
            "id": "paper-c0",
            "dense_vector": [0.0, 1.0],
            "content": "attention",
            "project_ids": ["project-1"],
            "version_status": "active",
            "content_type": "text",
        },
    )
    store.insert(entities)
    searcher = HybridSearch(embedder=ConstantEmbedder(), vector_store=store)
    assert "paper-c0" not in {
        item["id"] for item in searcher.search("attention", ["project-1"], top_k=5)
    }

    async def hydrate(results: list[dict], project_ids: list[str]) -> list[dict]:
        return await hydrate_search_results(
            results,
            project_ids,
            db_session_factory=database(sessions),
        )

    tools = registry(sessions, searcher=searcher, hydrator=hydrate)
    result = await tools.execute(
        "search_knowledge",
        {"query": "attention", "doc_types": ["paper"], "top_k": 5},
        project_ids=["project-1"],
        session_id="s",
    )
    assert [item["id"] for item in result["results"]] == ["paper-c0"]
    assert set(result["results"][0]["retrieval_channels"]) == {"bm25", "dense"}
    with sessions() as session:
        session.get(Document, "doc-paper").tags = ["corrected"]
        session.commit()
    corrected = await tools.execute(
        "search_knowledge",
        {"query": "attention", "tags": ["corrected"]},
        project_ids=["project-1"],
        session_id="s",
    )
    assert [item["id"] for item in corrected["results"]] == ["paper-c0"]
    assert corrected["results"][0]["tags"] == ["corrected"]
    for project_ids, tags in [(["project-2"], ["corrected"]), (["project-1"], ["missing"])]:
        empty = await tools.execute(
            "search_knowledge",
            {"query": "attention", "tags": tags},
            project_ids=project_ids,
            session_id="s",
        )
        assert empty["results"] == []
