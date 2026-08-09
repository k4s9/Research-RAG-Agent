from pathlib import Path

import pytest

from src.core.ingest.pipeline import DocumentIngestPipeline
from src.db.models import Document, Project


pytestmark = pytest.mark.unit


class FakeMarkdownParser:
    async def parse(self, file_path: str) -> dict:
        return {
            "file_type": "markdown",
            "sections": [
                {
                    "heading": "Evidence",
                    "level": 1,
                    "content": "Verified evidence.",
                    "start_line": 1,
                    "end_line": 2,
                    "section_path": ["Evidence"],
                }
            ],
            "total_sections": 1,
            "total_blocks": 1,
        }


class IdentityCleaner:
    def clean(self, parsed_content: dict) -> dict:
        return parsed_content


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


class FakeVectorStore:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.entities: list[dict] = []
        self.deleted_ids: list[str] = []

    def insert(self, entities: list[dict]) -> dict:
        self.entities.extend(entities)
        if self.fail:
            raise RuntimeError("vector store unavailable")
        return {"insert_count": len(entities)}

    def delete(self, ids: list[str]) -> None:
        self.deleted_ids.extend(ids)


class FakeSession:
    def __init__(self) -> None:
        self.documents: dict[str, Document] = {}
        self.project = Project(id="project-1", name="Test project")
        self.executed = []

    async def get(self, model: type, identifier: str):
        if model is Project:
            return self.project if identifier == self.project.id else None
        if model is Document:
            return self.documents.get(identifier)
        return None

    def add(self, instance: object) -> None:
        if isinstance(instance, Document):
            self.documents[instance.id] = instance

    def add_all(self, instances: list[object]) -> None:
        del instances

    async def commit(self) -> None:
        return None

    async def execute(self, statement):
        if getattr(statement, "is_select", False):
            return FakeResult(next(iter(self.documents.values()), None))
        self.executed.append(statement)


class FakeResult:
    def __init__(self, value: Document | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Document | None:
        return self.value


def session_factory(session: FakeSession):
    async def provider():
        yield session

    return provider


@pytest.mark.asyncio
async def test_pipeline_marks_document_ready_after_confirmed_vector_insert(tmp_path: Path) -> None:
    session = FakeSession()
    vector_store = FakeVectorStore()
    pipeline = DocumentIngestPipeline(
        markdown_parser=FakeMarkdownParser(),
        cleaner=IdentityCleaner(),
        embedder=FakeEmbedder(),
        vector_store=vector_store,
        db_session_factory=session_factory(session),
    )

    source = tmp_path / "notes.md"
    source.write_text("Verified evidence.", encoding="utf-8")
    result = await pipeline.process_document(str(source), ["project-1"])
    document = session.documents[result["document_id"]]

    assert result["status"] == "ready"
    assert document.status == "ready"
    assert document.parse_metadata["stage"] == "ready"
    assert document.parse_metadata["chunk_count"] == 1
    assert len(vector_store.entities) == 1


@pytest.mark.asyncio
async def test_pipeline_marks_document_failed_and_compensates_after_vector_error(
    tmp_path: Path,
) -> None:
    session = FakeSession()
    vector_store = FakeVectorStore(fail=True)
    pipeline = DocumentIngestPipeline(
        markdown_parser=FakeMarkdownParser(),
        cleaner=IdentityCleaner(),
        embedder=FakeEmbedder(),
        vector_store=vector_store,
        db_session_factory=session_factory(session),
    )

    with pytest.raises(RuntimeError, match="vector store unavailable"):
        source = tmp_path / "notes.md"
        source.write_text("Verified evidence.", encoding="utf-8")
        await pipeline.process_document(str(source), ["project-1"])

    document = next(iter(session.documents.values()))
    assert document.status == "failed"
    assert document.parse_metadata["stage"] == "failed"
    assert "vector store unavailable" in document.parse_metadata["error"]
    assert vector_store.deleted_ids == [entity["id"] for entity in vector_store.entities]
    assert len(session.executed) == 1


@pytest.mark.asyncio
async def test_pipeline_deduplicates_ready_document_by_content_hash(tmp_path: Path) -> None:
    source = tmp_path / "notes.md"
    source.write_text("Verified evidence.", encoding="utf-8")
    session = FakeSession()
    vector_store = FakeVectorStore()
    pipeline = DocumentIngestPipeline(
        markdown_parser=FakeMarkdownParser(),
        cleaner=IdentityCleaner(),
        embedder=FakeEmbedder(),
        vector_store=vector_store,
        db_session_factory=session_factory(session),
    )

    first = await pipeline.process_document(str(source), ["project-1"])
    second = await pipeline.process_document(str(source), ["project-1"])

    assert second == {**first, "deduplicated": True}
    assert len(session.documents) == 1
    assert len(vector_store.entities) == 1
