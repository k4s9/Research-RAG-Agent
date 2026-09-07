from typing import Any

from sqlalchemy import select

from src.core.citations import source_locator
from src.db.models import Chunk, Document, project_document
from src.db.postgres import get_db


async def hydrate_search_results(
    results: list[dict[str, Any]],
    project_ids: list[str],
    db_session_factory=None,
    session=None,
) -> list[dict[str, Any]]:
    """Replace vector-index payloads with project-authorized PostgreSQL content."""
    chunk_ids = [result.get("id") for result in results if result.get("id")]
    if not chunk_ids or not project_ids:
        return []

    async def load(active_session):
        rows = await active_session.execute(
            select(Chunk, Document)
            .join(Document, Chunk.document_id == Document.id)
            .join(project_document, project_document.c.document_id == Document.id)
            .where(
                Chunk.id.in_(chunk_ids),
                project_document.c.project_id.in_(project_ids),
            ),
        )
        return {chunk.id: (chunk, document) for chunk, document in rows.all()}

    if session is not None:
        hydrated = await load(session)
    else:
        hydrated = {}
        provider = db_session_factory or get_db
        async for active_session in provider():
            hydrated = await load(active_session)
            break

    output = []
    for result in results:
        record = hydrated.get(result.get("id"))
        if record is None:
            continue
        chunk, document = record
        output.append(
            {
                **result,
                "id": chunk.id,
                "content": chunk.content,
                "content_type": chunk.content_type,
                "version_status": chunk.version_status,
                "filename": document.filename,
                "source": document.filename,
                "locator": source_locator(
                    document.filename,
                    document.file_type,
                    chunk.chunk_metadata,
                ),
                "created_at": document.created_at.isoformat() if document.created_at else "",
            },
        )
    return output
