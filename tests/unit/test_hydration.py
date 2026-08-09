from datetime import datetime

import pytest

from src.core.retrieval.hydration import hydrate_search_results
from src.db.models import Chunk, Document

pytestmark = pytest.mark.unit


class FakeRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, statement):
        return FakeRows(self.rows)


def provider(rows):
    async def session_provider():
        yield FakeSession(rows)

    return session_provider


@pytest.mark.asyncio
async def test_hydration_uses_authoritative_content_and_locator() -> None:
    document = Document(
        id="doc-1",
        filename="paper.pdf",
        file_type="pdf",
        file_path="/tmp/paper.pdf",
        status="ready",
        created_at=datetime(2026, 1, 1),
    )
    chunk = Chunk(
        id="chunk-1",
        document_id="doc-1",
        chunk_index=0,
        content="Authoritative text",
        content_type="text",
        chunk_metadata={"page_start": 4, "page_end": 4},
        version_status="active",
    )
    results = await hydrate_search_results(
        [{"id": "chunk-1", "score": 0.8, "content": "stale"}],
        ["project-1"],
        provider([(chunk, document)]),
    )

    assert results[0]["content"] == "Authoritative text"
    assert results[0]["filename"] == "paper.pdf"
    assert results[0]["locator"]["page_start"] == 4


@pytest.mark.asyncio
async def test_hydration_drops_unresolved_or_unscoped_hits() -> None:
    assert await hydrate_search_results([{"id": "chunk-1"}], [], provider([])) == []
    assert await hydrate_search_results([{"id": "chunk-1"}], ["project-1"], provider([])) == []
