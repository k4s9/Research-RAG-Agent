"""Tool registry for the research assistant.

Tools are intentionally small and permission-aware. The model may request a
project subset, but it can never widen the project scope supplied by the API
caller. Search results and source sections are hydrated from PostgreSQL before
being returned to the model.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from loguru import logger
from sqlalchemy import select

from src.config.settings import settings
from src.core.citations import source_locator
from src.core.document_view import (
    DEFAULT_RANGE_CHARS,
    MAX_RANGE_CHARS,
    build_document_outline,
    chunk_page_range,
    select_document_range,
)
from src.core.ingest.metadata import DOC_TYPES
from src.core.retrieval.hydration import hydrate_search_results
from src.db.models import Chunk, Conversation, Document, Memory, Project, project_document
from src.db.postgres import get_db

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "Search project knowledge with hybrid BM25 and dense retrieval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "project_ids": {"type": "array", "items": {"type": "string"}},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "include_outdated": {"type": "boolean"},
                    "content_types": {"type": "array", "items": {"type": "string"}},
                    "doc_types": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(DOC_TYPES)},
                    },
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_document_section",
            "description": "Fetch authoritative text and its page or section locator for a chunk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "string", "minLength": 1},
                    "document_id": {"type": "string", "minLength": 1},
                    "page_start": {"type": "integer", "minimum": 1},
                    "section_path": {"type": "array", "items": {"type": "string"}},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": (
                "List the materials in the project registry, optionally filtered by "
                "document type, tag, year, or ingest batch. Use it to answer "
                "'what have I uploaded'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_ids": {"type": "array", "items": {"type": "string"}},
                    "doc_type": {"type": "string", "enum": list(DOC_TYPES)},
                    "tag": {"type": "string"},
                    "year": {"type": "integer", "minimum": 1900, "maximum": 2100},
                    "ingest_batch_id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_document_outline",
            "description": "Return the section/page skeleton of one document for reading planning.",
            "parameters": {
                "type": "object",
                "properties": {"document_id": {"type": "string", "minLength": 1}},
                "required": ["document_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document_range",
            "description": (
                "Read consecutive chunks of one document in document order, by page range "
                "or section path. Use it for close reading instead of scattered search hits."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "minLength": 1},
                    "page_start": {"type": "integer", "minimum": 1},
                    "page_end": {"type": "integer", "minimum": 1},
                    "section_path": {"type": "array", "items": {"type": "string"}},
                    "max_chars": {"type": "integer", "minimum": 200, "maximum": MAX_RANGE_CHARS},
                    "start_chunk_index": {"type": "integer", "minimum": 0},
                    "start_char": {"type": "integer", "minimum": 0},
                },
                "required": ["document_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": (
                "Save a confirmed project memory such as a decision, todo, milestone, or insight."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_type": {
                        "type": "string",
                        "enum": ["milestone", "todo", "decision", "insight"],
                    },
                    "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "original_context": {"type": "string", "minLength": 1},
                    "project_ids": {"type": "array", "items": {"type": "string"}},
                    "source_chunk_id": {"type": "string"},
                },
                "required": ["memory_type", "summary", "original_context"],
                "additionalProperties": False,
            },
        },
    },
]


class ToolExecutionError(ValueError):
    """A safe, model-visible tool validation or authorization failure."""


class ResearchToolRegistry:
    def __init__(
        self,
        searcher: object,
        hydrator: Callable[..., Awaitable[list[dict[str, Any]]]] = hydrate_search_results,
        db_session_factory: Callable[[], AsyncIterator[Any]] | None = None,
    ) -> None:
        self.searcher = searcher
        self.hydrator = hydrator
        self.db_session_factory = db_session_factory or get_db

    @staticmethod
    def _scope(requested: object, allowed: list[str]) -> list[str]:
        if requested is None:
            return list(allowed)
        if not isinstance(requested, list) or not all(isinstance(item, str) for item in requested):
            raise ToolExecutionError("project_ids must be an array of strings")
        if not set(requested).issubset(set(allowed)):
            raise ToolExecutionError("tool project scope exceeds the caller project scope")
        return list(dict.fromkeys(requested))

    @staticmethod
    def _string_list(value: object, field: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ToolExecutionError(f"{field} must be an array of strings")
        return [item.strip() for item in value if item.strip()]

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        project_ids: list[str],
        session_id: str,
    ) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ToolExecutionError("tool arguments must be an object")
        handlers = {
            "search_knowledge": self.search_knowledge,
            "get_document_section": self.get_document_section,
            "list_documents": self.list_documents,
            "get_document_outline": self.get_document_outline,
            "read_document_range": self.read_document_range,
            "save_memory": self.save_memory,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ToolExecutionError(f"unknown tool: {name}")
        allowed_arguments = {
            "search_knowledge": {
                "query",
                "project_ids",
                "top_k",
                "include_outdated",
                "content_types",
                "doc_types",
                "tags",
            },
            "get_document_section": {"chunk_id", "document_id", "page_start", "section_path"},
            "list_documents": {
                "project_ids",
                "doc_type",
                "tag",
                "year",
                "ingest_batch_id",
                "limit",
            },
            "get_document_outline": {"document_id"},
            "read_document_range": {
                "document_id",
                "page_start",
                "page_end",
                "section_path",
                "max_chars",
                "start_chunk_index",
                "start_char",
            },
            "save_memory": {
                "memory_type",
                "summary",
                "original_context",
                "project_ids",
                "source_chunk_id",
            },
        }
        unknown = set(arguments) - allowed_arguments[name]
        if unknown:
            raise ToolExecutionError(f"unknown tool arguments: {sorted(unknown)}")
        return await handler(arguments, project_ids=project_ids, session_id=session_id)

    async def search_knowledge(
        self,
        arguments: dict[str, Any],
        *,
        project_ids: list[str],
        session_id: str,
    ) -> dict[str, Any]:
        del session_id
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolExecutionError("query must be a non-empty string")
        scoped_projects = self._scope(arguments.get("project_ids"), project_ids)
        requested_top_k = arguments.get("top_k", settings.agent_search_top_k)
        if type(requested_top_k) is not int or not 1 <= requested_top_k <= 20:
            raise ToolExecutionError("top_k must be between 1 and 20")
        top_k = min(requested_top_k, settings.agent_search_top_k)
        if "include_outdated" in arguments and type(arguments["include_outdated"]) is not bool:
            raise ToolExecutionError("include_outdated must be boolean")
        content_types = arguments.get("content_types")
        if content_types is not None and (
            not isinstance(content_types, list)
            or not all(isinstance(item, str) for item in content_types)
        ):
            raise ToolExecutionError("content_types must be an array of strings")
        doc_types = self._string_list(arguments.get("doc_types"), "doc_types")
        tags = self._string_list(arguments.get("tags"), "tags")
        if any(doc_type not in DOC_TYPES for doc_type in doc_types):
            raise ToolExecutionError("doc_types must contain only paper/report/note/other")
        filters: dict[str, Any] = {}
        if doc_types or tags:
            filters["chunk_ids"] = await self._matching_chunk_ids(scoped_projects, doc_types, tags)
            if not filters["chunk_ids"]:
                return {"query": query, "results": [], "result_count": 0, "retrieval_degraded": []}
        results = await asyncio.to_thread(self.searcher.search,
            query=query.strip(),
            project_ids=scoped_projects,
            top_k=top_k,
            include_outdated=bool(arguments.get("include_outdated", False)),
            content_types=content_types,
            **filters,
        )
        hydrated_results = results
        hydrated = await self.hydrator(hydrated_results, scoped_projects)
        if doc_types:
            hydrated = [item for item in hydrated if item.get("doc_type") in doc_types]
        if tags:
            hydrated = [item for item in hydrated if set(tags).intersection(item.get("tags") or [])]
        hydrated = hydrated[:top_k]
        return {
            "query": query,
            "requested_top_k": requested_top_k,
            "effective_top_k": top_k,
            "results": hydrated,
            "result_count": len(hydrated),
            "retrieval_degraded": sorted(
                {
                    flag
                    for result in hydrated_results
                    for flag in result.get("retrieval_degraded", [])
                },
            ),
        }

    async def _matching_chunk_ids(
        self,
        project_ids: list[str],
        doc_types: list[str],
        tags: list[str],
    ) -> list[str]:
        """Resolve current metadata before either retrieval channel applies Top-K."""
        async for session in self.db_session_factory():
            statement = select(Document.id, Document.tags).where(
                Document.projects.any(Project.id.in_(project_ids)),
            )
            if doc_types:
                statement = statement.where(Document.doc_type.in_(doc_types))
            rows = (await session.execute(statement)).all()
            document_ids = [
                doc_id
                for doc_id, doc_tags in rows
                if not tags or set(tags).intersection(doc_tags or [])
            ]
            if not document_ids:
                return []
            result = await session.execute(
                select(Chunk.id).where(Chunk.document_id.in_(document_ids)),
            )
            return list(result.scalars().all())
        raise ToolExecutionError("database session unavailable")

    async def get_document_section(
        self,
        arguments: dict[str, Any],
        *,
        project_ids: list[str],
        session_id: str,
    ) -> dict[str, Any]:
        del session_id
        chunk_id = arguments.get("chunk_id")
        document_id = arguments.get("document_id")
        page_start = arguments.get("page_start")
        section_path = arguments.get("section_path")
        if chunk_id is not None and (not isinstance(chunk_id, str) or not chunk_id.strip()):
            raise ToolExecutionError("chunk_id must be a non-empty string")
        if document_id is not None and (
            not isinstance(document_id, str) or not document_id.strip()
        ):
            raise ToolExecutionError("document_id must be a non-empty string")
        if chunk_id is None and document_id is None:
            raise ToolExecutionError("chunk_id or document_id is required")
        if page_start is not None and (type(page_start) is not int or page_start < 1):
            raise ToolExecutionError("page_start must be a positive integer")
        if section_path is not None and (
            not isinstance(section_path, list)
            or not all(isinstance(item, str) for item in section_path)
        ):
            raise ToolExecutionError("section_path must be an array of strings")

        async def load(session: object) -> tuple[Chunk, Document] | None:
            filters = [project_document.c.project_id.in_(project_ids)]
            if chunk_id:
                filters.append(Chunk.id == chunk_id)
            if document_id:
                filters.append(Document.id == document_id)
            statement = (
                select(Chunk, Document)
                .join(Document, Chunk.document_id == Document.id)
                .join(project_document, project_document.c.document_id == Document.id)
                .where(*filters)
            )
            result = await session.execute(statement)
            rows = result.all()
            for row_chunk, row_document in rows:
                metadata = row_chunk.chunk_metadata or {}
                if page_start is not None:
                    first_page = metadata.get("page_start", metadata.get("page_num"))
                    last_page = metadata.get("page_end", metadata.get("page_num"))
                    if first_page is None or not first_page <= page_start <= last_page:
                        continue
                if section_path is not None and metadata.get("section_path", []) != section_path:
                    continue
                return row_chunk, row_document
            return None

        record = None
        async for session in self.db_session_factory():
            record = await load(session)
            break
        if record is None:
            raise ToolExecutionError("document section not found in the caller project scope")
        chunk, document = record
        return {
            "chunk_id": chunk.id,
            "document_id": document.id,
            "document_hash": document.content_hash,
            "content": chunk.content,
            "content_type": chunk.content_type,
            "filename": document.filename,
            "locator": source_locator(document.filename, document.file_type, chunk.chunk_metadata),
            "version_status": chunk.version_status,
        }

    @staticmethod
    def _document_payload(document: Document) -> dict[str, Any]:
        return {
            "id": document.id,
            "document_id": document.id,
            "filename": document.filename,
            "title": document.title,
            "doc_type": document.doc_type,
            "year": document.year,
            "venue": document.venue,
            "authors": list(document.authors or []),
            "tags": list(document.tags or []),
            "summary": document.summary,
            "page_count": document.page_count,
            "ingest_batch_id": document.ingest_batch_id,
            "status": document.status,
        }

    async def _authorized_document(
        self,
        session: object,
        document_id: str,
        project_ids: list[str],
    ) -> Document | None:
        result = await session.execute(
            select(Document)
            .join(project_document, project_document.c.document_id == Document.id)
            .where(
                Document.id == document_id,
                project_document.c.project_id.in_(project_ids),
            ),
        )
        return result.scalars().first()

    async def _document_chunks(self, session: object, document_id: str) -> list[Chunk]:
        result = await session.execute(
            select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index.asc()),
        )
        return list(result.scalars().all())

    async def list_documents(
        self,
        arguments: dict[str, Any],
        *,
        project_ids: list[str],
        session_id: str,
    ) -> dict[str, Any]:
        """Registry lookup: "which papers/reports did I upload?" """
        del session_id
        scoped_projects = self._scope(arguments.get("project_ids"), project_ids)
        doc_type = arguments.get("doc_type")
        if doc_type is not None and doc_type not in DOC_TYPES:
            raise ToolExecutionError("doc_type must be one of paper/report/note/other")
        tag = arguments.get("tag")
        if tag is not None and (not isinstance(tag, str) or not tag.strip()):
            raise ToolExecutionError("tag must be a non-empty string")
        year = arguments.get("year")
        if year is not None and (type(year) is not int or not 1900 <= year <= 2100):
            raise ToolExecutionError("year must be between 1900 and 2100")
        ingest_batch_id = arguments.get("ingest_batch_id")
        if ingest_batch_id is not None and (
            not isinstance(ingest_batch_id, str) or not ingest_batch_id.strip()
        ):
            raise ToolExecutionError("ingest_batch_id must be a non-empty string")
        limit = arguments.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ToolExecutionError("limit must be between 1 and 50")

        documents: list[Document] = []
        async for session in self.db_session_factory():
            statement = select(Document).where(
                Document.projects.any(Project.id.in_(scoped_projects)),
            )
            if doc_type:
                statement = statement.where(Document.doc_type == doc_type)
            if year is not None:
                statement = statement.where(Document.year == year)
            if ingest_batch_id:
                statement = statement.where(Document.ingest_batch_id == ingest_batch_id)
            statement = statement.order_by(Document.created_at.desc(), Document.id)
            result = await session.execute(statement)
            documents = list(result.scalars().unique().all())
            break
        if tag:
            documents = [document for document in documents if tag in (document.tags or [])]
        total_matched = len(documents)
        selected = documents[:limit]
        return {
            "documents": [self._document_payload(document) for document in selected],
            "result_count": len(selected),
            "total_matched": total_matched,
        }

    async def get_document_outline(
        self,
        arguments: dict[str, Any],
        *,
        project_ids: list[str],
        session_id: str,
    ) -> dict[str, Any]:
        """Return the section/page skeleton used to plan close reading."""
        del session_id
        document_id = arguments.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            raise ToolExecutionError("document_id must be a non-empty string")
        scoped_projects = self._scope(arguments.get("project_ids"), project_ids)
        document = None
        chunks: list[Chunk] = []
        async for session in self.db_session_factory():
            document = await self._authorized_document(session, document_id, scoped_projects)
            if document is None:
                raise ToolExecutionError("document not found in the caller project scope")
            chunks = await self._document_chunks(session, document_id)
            break
        outline = build_document_outline(document, chunks)
        return {
            "document_id": document.id,
            "filename": document.filename,
            "title": document.title,
            "doc_type": document.doc_type,
            "outline": outline,
            "result_count": len(outline),
            "chunk_count": len(chunks),
        }

    async def read_document_range(
        self,
        arguments: dict[str, Any],
        *,
        project_ids: list[str],
        session_id: str,
    ) -> dict[str, Any]:
        """Read consecutive chunks in document order (page range or section path)."""
        del session_id
        document_id = arguments.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            raise ToolExecutionError("document_id must be a non-empty string")
        page_start = arguments.get("page_start")
        page_end = arguments.get("page_end")
        for field, value in (("page_start", page_start), ("page_end", page_end)):
            if value is not None and (type(value) is not int or value < 1):
                raise ToolExecutionError(f"{field} must be a positive integer")
        if page_start is not None and page_end is not None and page_end < page_start:
            raise ToolExecutionError("page_end must not be smaller than page_start")
        section_path = arguments.get("section_path")
        if section_path is not None and (
            not isinstance(section_path, list)
            or not section_path
            or not all(isinstance(item, str) and item.strip() for item in section_path)
        ):
            raise ToolExecutionError("section_path must be a non-empty array of strings")
        max_chars = arguments.get("max_chars", DEFAULT_RANGE_CHARS)
        if type(max_chars) is not int or not 200 <= max_chars <= MAX_RANGE_CHARS:
            raise ToolExecutionError(f"max_chars must be between 200 and {MAX_RANGE_CHARS}")
        start_chunk_index = arguments.get("start_chunk_index", 0)
        start_char = arguments.get("start_char", 0)
        for field, value in (("start_chunk_index", start_chunk_index), ("start_char", start_char)):
            if type(value) is not int or value < 0:
                raise ToolExecutionError(f"{field} must be a non-negative integer")
        scoped_projects = self._scope(arguments.get("project_ids"), project_ids)

        document = None
        chunks: list[Chunk] = []
        async for session in self.db_session_factory():
            document = await self._authorized_document(session, document_id, scoped_projects)
            if document is None:
                raise ToolExecutionError("document not found in the caller project scope")
            chunks = await self._document_chunks(session, document_id)
            break
        selected, next_cursor = select_document_range(
            chunks,
            page_start=page_start,
            page_end=page_end,
            section_path=section_path,
            max_chars=max_chars,
            start_chunk_index=start_chunk_index,
            start_char=start_char,
        )
        if not selected:
            raise ToolExecutionError("no document content in the requested range")
        payload = []
        for chunk in selected:
            first_page, last_page = chunk_page_range(chunk)
            payload.append(
                {
                    "chunk_id": chunk.id,
                    "chunk_index": chunk.chunk_index,
                    "page_start": first_page,
                    "page_end": last_page,
                    "content_type": chunk.content_type,
                    "locator": source_locator(
                        document.filename,
                        document.file_type,
                        chunk.chunk_metadata,
                    ),
                    "content": chunk.content,
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                },
            )
        return {
            "document_id": document.id,
            "filename": document.filename,
            "title": document.title,
            "chunks": payload,
            "document_hash": document.content_hash,
            "content": "\n\n".join(chunk.content for chunk in selected),
            "chunk_count": len(payload),
            "truncated": next_cursor is not None,
            "next_cursor": next_cursor,
        }

    async def save_memory(
        self,
        arguments: dict[str, Any],
        *,
        project_ids: list[str],
        session_id: str,
    ) -> dict[str, Any]:
        memory_type = arguments.get("memory_type")
        if memory_type not in {"milestone", "todo", "decision", "insight"}:
            raise ToolExecutionError("invalid memory_type")
        summary = arguments.get("summary")
        context = arguments.get("original_context")
        if not isinstance(summary, str) or not summary.strip():
            raise ToolExecutionError("summary must be a non-empty string")
        if not isinstance(context, str) or not context.strip():
            raise ToolExecutionError("original_context must be a non-empty string")
        scoped_projects = self._scope(arguments.get("project_ids"), project_ids)
        source_chunk_id = arguments.get("source_chunk_id")
        if source_chunk_id is not None and not isinstance(source_chunk_id, str):
            raise ToolExecutionError("source_chunk_id must be a string")

        async for session in self.db_session_factory():
            projects = []
            if scoped_projects:
                result = await session.execute(
                    select(Project).where(Project.id.in_(scoped_projects)),
                )
                projects = list(result.scalars().all())
                if len(projects) != len(set(scoped_projects)):
                    raise ToolExecutionError("one or more projects do not exist")
            if source_chunk_id:
                source = await session.execute(
                    select(Chunk.id)
                    .join(Document, Chunk.document_id == Document.id)
                    .join(project_document, project_document.c.document_id == Document.id)
                    .where(
                        Chunk.id == source_chunk_id,
                        project_document.c.project_id.in_(scoped_projects),
                    ),
                )
                if source.first() is None:
                    raise ToolExecutionError("source chunk is outside the caller project scope")
            source_conversation_id = None
            if session_id:
                conversation_result = await session.execute(
                    select(Conversation)
                    .where(Conversation.session_id == session_id)
                    .order_by(Conversation.timestamp.desc()),
                )
                conversation = conversation_result.scalars().first()
                if conversation is not None:
                    source_conversation_id = conversation.id
            memory = Memory(
                memory_type=memory_type,
                summary=summary.strip(),
                original_context=context.strip(),
                source_conversation_id=source_conversation_id,
                source_chunk_id=source_chunk_id,
                projects=projects,
            )
            session.add(memory)
            await session.commit()
            response = {
                "memory_id": memory.id,
                "memory_type": memory.memory_type,
                "summary": memory.summary,
                "project_ids": scoped_projects,
                "version_status": memory.version_status,
            }
            try:
                embedder = getattr(self.searcher, "embedder", None)
                vector_store = getattr(self.searcher, "vector_store", None)
                if embedder is not None and vector_store is not None:
                    vector = embedder.embed([f"{memory.summary}\n{memory.original_context}"])[0]
                    vector_store.insert(
                        [
                            {
                                "id": memory.id,
                                "entity_type": "memory",
                                "dense_vector": vector,
                                "project_ids": scoped_projects,
                                "version_status": memory.version_status,
                                "content_type": memory.memory_type,
                                "content": memory.summary,
                                "created_at": int(memory.created_at.timestamp()),
                            },
                        ],
                    )
                    response["indexed"] = True
                else:
                    response["indexed"] = False
            except Exception as exc:
                # The relational record remains authoritative; surface the
                # indexing degradation to the caller instead of hiding it.
                logger.warning(f"记忆向量索引失败: {exc}")
                response["indexed"] = False
                response["indexing_error"] = str(exc)
            return response
        raise ToolExecutionError("database session unavailable")
