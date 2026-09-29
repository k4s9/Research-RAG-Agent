from collections.abc import AsyncIterator, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.citations import source_locator
from src.core.retrieval.bm25 import MAX_RETRIEVAL_CANDIDATES, RetrievalScopeError
from src.db.models import Chunk, Document, Project, project_document
from src.db.postgres import get_db


async def resolve_search_chunk_ids(
    project_ids: list[str],
    *,
    include_outdated: bool = False,
    content_types: list[str] | None = None,
    doc_types: list[str] | None = None,
    tags: list[str] | None = None,
    db_session_factory: Callable[[], AsyncIterator[AsyncSession]] | None = None,
    session: AsyncSession | None = None,
) -> list[str]:
    """Resolve the complete authorized scope before either channel applies Top-K.

    Project membership, publication/version state and metadata are PostgreSQL
    facts. Vector-index copies may be stale after deduplication or corrections.
    The bound applies before the portable Python JSON-tag check.
    """
    if not project_ids:
        return []

    async def load(active_session):
        statement = (
            select(Chunk.id, Document.tags)
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.status == "ready", Document.projects.any(Project.id.in_(project_ids)))
        )
        if not include_outdated:
            statement = statement.where(Chunk.version_status == "active")
        if content_types:
            statement = statement.where(Chunk.content_type.in_(content_types))
        if doc_types:
            statement = statement.where(Document.doc_type.in_(doc_types))
        rows = (await active_session.execute(statement.limit(MAX_RETRIEVAL_CANDIDATES + 1))).all()
        if len(rows) > MAX_RETRIEVAL_CANDIDATES:
            raise RetrievalScopeError(
                "当前检索最多支持 10000 个候选块（标签筛选前）；"
                "请缩小项目、文档类型或内容类型范围。未截断候选。",
            )
        return [identifier for identifier, doc_tags in rows
                if not tags or set(tags).intersection(doc_tags or [])]

    if session is not None:
        return await load(session)
    provider = db_session_factory or get_db
    async for active_session in provider():
        return await load(active_session)
    raise RuntimeError("database session unavailable")


async def hydrate_search_results(
    results: list[dict[str, Any]],
    project_ids: list[str],
    db_session_factory: Callable[[], AsyncIterator[AsyncSession]] | None = None,
    session: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    """Replace vector-index payloads with project-authorized PostgreSQL content."""
    chunk_ids = [result.get("id") for result in results if result.get("id")]
    if not chunk_ids or not project_ids:
        return []

    async def load(active_session: AsyncSession) -> dict[str, tuple[Chunk, Document, set[str]]]:
        rows = await active_session.execute(
            select(Chunk, Document, project_document.c.project_id)
            .join(Document, Chunk.document_id == Document.id)
            .join(project_document, project_document.c.document_id == Document.id)
            .where(
                Chunk.id.in_(chunk_ids),
                project_document.c.project_id.in_(project_ids),
                Document.status == "ready",
            ),
        )
        records = {}
        for row in rows.all():
            # SQLAlchemy returns the membership column; lightweight test
            # providers may still return the historical two-item shape.
            if len(row) == 3:
                chunk, document, project_id = row
            else:
                chunk, document = row
                project_id = project_ids[0]
            records.setdefault(chunk.id, (chunk, document, set()))[2].add(project_id)
        return records

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
        chunk, document, current_projects = record
        output.append(
            {
                **result,
                "id": chunk.id,
                "project_ids": sorted(current_projects),
                "content": chunk.content,
                "content_type": chunk.content_type,
                "version_status": chunk.version_status,
                "filename": document.filename,
                "source": document.filename,
                "document_id": document.id,
                "document_hash": document.content_hash,
                "title": document.title,
                "doc_type": document.doc_type,
                "tags": list(document.tags or []),
                "year": document.year,
                "locator": source_locator(
                    document.filename,
                    document.file_type,
                    chunk.chunk_metadata,
                ),
                "created_at": document.created_at.isoformat() if document.created_at else "",
            },
        )
    return output
