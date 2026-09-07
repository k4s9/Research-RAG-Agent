"""Tool registry for the research assistant.

Tools are intentionally small and permission-aware. The model may request a
project subset, but it can never widen the project scope supplied by the API
caller. Search results and source sections are hydrated from PostgreSQL before
being returned to the model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from loguru import logger
from sqlalchemy import select

from src.core.citations import source_locator
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
            },
            "get_document_section": {"chunk_id", "document_id", "page_start", "section_path"},
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
        top_k = arguments.get("top_k", 5)
        if not isinstance(top_k, int) or not 1 <= top_k <= 20:
            raise ToolExecutionError("top_k must be between 1 and 20")
        content_types = arguments.get("content_types")
        if content_types is not None and (
            not isinstance(content_types, list)
            or not all(isinstance(item, str) for item in content_types)
        ):
            raise ToolExecutionError("content_types must be an array of strings")
        results = self.searcher.search(
            query=query.strip(),
            project_ids=scoped_projects,
            top_k=top_k,
            include_outdated=bool(arguments.get("include_outdated", False)),
            content_types=content_types,
        )
        hydrated_results = results
        hydrated = await self.hydrator(hydrated_results, scoped_projects)
        hydrated = [{**item, "source_id": f"S{index + 1}"} for index, item in enumerate(hydrated)]
        return {
            "query": query,
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
        if page_start is not None and (not isinstance(page_start, int) or page_start < 1):
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
            "content": chunk.content,
            "content_type": chunk.content_type,
            "filename": document.filename,
            "locator": source_locator(document.filename, document.file_type, chunk.chunk_metadata),
            "version_status": chunk.version_status,
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
