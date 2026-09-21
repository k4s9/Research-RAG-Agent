"""Asynchronous LLM enrichment: document summary and tags.

Enrichment is best effort. A failure only logs a warning: the document stays
``ready`` because the summary and tags are derived metadata, not evidence.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

from loguru import logger
from sqlalchemy import select

from src.config.settings import settings
from src.db.models import Chunk, Document
from src.db.postgres import get_db
from src.utils.async_llm_client import AsyncLLMClient

MAX_SOURCE_CHUNKS = 4
MAX_SOURCE_CHARS = 4000
MAX_SUMMARY_CHARS = 1000
MAX_TAGS = 8
MAX_TAG_CHARS = 40

ENRICHMENT_PROMPT = """阅读下面的文档片段，只输出 JSON 对象（不要输出解释或代码块围栏）：
{{"summary": "不超过 200 字的中文摘要", "tags": ["标签1", "标签2"]}}

要求：
- summary 只能描述片段中真实出现的内容，不得编造；
- tags 为 1-8 个短标签，优先使用文档中的术语；
- 只输出 JSON 对象。

文件名：{filename}
标题：{title}

片段：
{content}
"""

_JSON_BLOCK_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


def parse_enrichment_payload(payload: str) -> dict[str, Any] | None:
    """Extract and validate ``{"summary": ..., "tags": [...]}`` from an LLM reply."""
    if not isinstance(payload, str):
        return None
    match = _JSON_BLOCK_PATTERN.search(payload)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    summary = parsed.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    tags: list[str] = []
    raw_tags = parsed.get("tags")
    if isinstance(raw_tags, list):
        for tag in raw_tags:
            if not isinstance(tag, str):
                continue
            cleaned = " ".join(tag.split())[:MAX_TAG_CHARS]
            if cleaned and cleaned not in tags:
                tags.append(cleaned)
            if len(tags) >= MAX_TAGS:
                break
    return {"summary": " ".join(summary.split())[:MAX_SUMMARY_CHARS], "tags": tags}


class EnrichmentLLM(Protocol):
    async def generate(self, prompt: str) -> str: ...


class DocumentEnricher:
    """Generate summary + tags for a document without blocking ingestion."""

    def __init__(
        self,
        llm_client: EnrichmentLLM | None = None,
        db_session_factory: Callable[[], AsyncIterator[Any]] | None = None,
    ) -> None:
        self._llm_client = llm_client
        self.db_session_factory = db_session_factory or get_db

    @property
    def llm_client(self) -> EnrichmentLLM:
        if self._llm_client is None:
            self._llm_client = AsyncLLMClient()
        return self._llm_client

    async def _load_source(self, document_id: str) -> tuple[Document | None, str]:
        document: Document | None = None
        chunks: list[Chunk] = []
        async for session in self.db_session_factory():
            document = await session.get(Document, document_id)
            if document is None:
                break
            result = await session.execute(
                select(Chunk)
                .where(Chunk.document_id == document_id)
                .order_by(Chunk.chunk_index.asc())
                .limit(MAX_SOURCE_CHUNKS),
            )
            chunks = list(result.scalars().all())
            break
        text = "\n\n".join(chunk.content for chunk in chunks)[:MAX_SOURCE_CHARS]
        return document, text

    async def _store(self, document_id: str, enrichment: dict[str, Any]) -> None:
        async for session in self.db_session_factory():
            result = await session.execute(
                select(Document).where(Document.id == document_id).with_for_update(),
            )
            document = result.scalar_one_or_none()
            if document is None:
                break
            document.summary = enrichment["summary"]
            # A manual PATCH may have run while the model was generating.
            if document.tags is None:
                document.tags = enrichment["tags"]
            session.add(document)
            await session.commit()
            break

    async def enrich_document(
        self,
        document_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(
                self._enrich_document(document_id, force=force),
                timeout=settings.document_enrichment_timeout_seconds,
            )
        except Exception as exc:
            logger.warning(f"文档摘要生成失败（不影响 ready）: {type(exc).__name__}: {exc}")
            return None

    async def _enrich_document(
        self,
        document_id: str,
        *,
        force: bool,
    ) -> dict[str, Any] | None:
        document, text = await self._load_source(document_id)
        if document is None:
            logger.warning(f"文档不存在，跳过摘要生成: {document_id}")
            return None
        if document.summary and not force:
            return {
                "summary": document.summary,
                "tags": list(document.tags or []),
                "skipped": True,
            }
        if not text.strip():
            logger.warning(f"文档没有可摘要的正文，跳过: {document_id}")
            return None
        prompt = ENRICHMENT_PROMPT.format(
            filename=document.filename,
            title=document.title or document.filename,
            content=text,
        )
        payload = await self.llm_client.generate(prompt=prompt)
        enrichment = parse_enrichment_payload(payload)
        if enrichment is None:
            logger.warning(f"文档摘要响应不是合法 JSON，忽略: {document_id}")
            return None
        try:
            await self._store(document_id, enrichment)
        except Exception as exc:
            logger.warning(f"文档摘要写库失败（不影响 ready）: {exc}")
            return None
        logger.info(f"文档摘要与标签已生成: {document_id}")
        return enrichment
