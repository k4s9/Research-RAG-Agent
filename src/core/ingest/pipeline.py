import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, select

from src.core.ingest.cleaner import DocumentCleaner
from src.core.ingest.markdown_chunker import MarkdownChunker
from src.core.ingest.markdown_parser import EnhancedMarkdownParser
from src.core.ingest.pdf_chunker import PDFChunker
from src.core.ingest.pdf_parser import PDFParser
from src.core.retrieval.embedder import Qwen3Embedder
from src.db.models import Chunk, Document, Project
from src.db.postgres import get_db as get_db_session


SUPPORTED_FILE_TYPES = {".pdf": "pdf", ".md": "markdown", ".markdown": "markdown"}


class DocumentIngestPipeline:
    """Parse, persist, and index a document while retaining source locators."""

    def __init__(
        self,
        chunk_size: int = 384,
        overlap: int = 128,
        *,
        pdf_parser: Optional[PDFParser] = None,
        markdown_parser: Optional[EnhancedMarkdownParser] = None,
        cleaner: Optional[DocumentCleaner] = None,
        embedder: Optional[Qwen3Embedder] = None,
        vector_store: Any = None,
        db_session_factory: Any = None,
    ):
        self.pdf_parser = pdf_parser or PDFParser()
        self.markdown_parser = markdown_parser or EnhancedMarkdownParser()
        self.cleaner = cleaner or DocumentCleaner()
        self.pdf_chunker = PDFChunker(chunk_size=chunk_size, overlap=overlap)
        self.markdown_chunker = MarkdownChunker(chunk_size=chunk_size, overlap=overlap)
        self.embedder = embedder or Qwen3Embedder()
        self.vector_store = vector_store
        self.db_session_factory = db_session_factory or get_db_session

    @staticmethod
    def _file_type(file_path: str) -> str:
        suffix = Path(file_path).suffix.lower()
        if suffix not in SUPPORTED_FILE_TYPES:
            raise ValueError(f"不支持的文件类型: {suffix}")
        return SUPPORTED_FILE_TYPES[suffix]

    def _get_chunker(self, file_type: str):
        if file_type == "pdf":
            return self.pdf_chunker
        if file_type == "markdown":
            return self.markdown_chunker
        raise ValueError(f"不支持的分块文件类型: {file_type}")

    async def _create_document(
        self,
        document_id: str,
        file_path: str,
        file_type: str,
        content_hash: str,
        project_ids: List[str],
    ) -> tuple[str, Optional[Dict[str, Any]]]:
        async for session in self.db_session_factory():
            projects = []
            for project_id in project_ids:
                project = await session.get(Project, project_id)
                if project is None:
                    raise ValueError(f"项目不存在: {project_id}")
                projects.append(project)
            result = await session.execute(
                select(Document).where(Document.content_hash == content_hash)
            )
            existing = (
                result.scalar_one_or_none() if hasattr(result, "scalar_one_or_none") else None
            )
            if existing is not None:
                for project in projects:
                    if project not in existing.projects:
                        existing.projects.append(project)
                await session.commit()
                if existing.status in {"ready", "processing"}:
                    metadata = existing.parse_metadata or {}
                    return existing.id, {
                        "document_id": existing.id,
                        "status": existing.status,
                        "chunk_count": metadata.get("chunk_count", 0),
                        "deduplicated": True,
                    }
                existing.file_path = file_path
                existing.filename = Path(file_path).name
                existing.status = "processing"
                existing.parse_metadata = {"stage": "parsing"}
                await session.commit()
                return existing.id, None
            now = datetime.now(timezone.utc)
            session.add(
                Document(
                    id=document_id,
                    filename=Path(file_path).name,
                    file_type=file_type,
                    file_path=file_path,
                    content_hash=content_hash,
                    status="processing",
                    parse_metadata={"stage": "parsing"},
                    projects=projects,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
            break
        return document_id, None

    @staticmethod
    def _content_hash(file_path: str) -> str:
        digest = hashlib.sha256()
        with Path(file_path).open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    async def _set_status(
        self, document_id: str, status: str, stage: str, error: Optional[str] = None
    ) -> None:
        async for session in self.db_session_factory():
            document = await session.get(Document, document_id)
            if document is None:
                break
            metadata = dict(document.parse_metadata or {})
            metadata["stage"] = stage
            if error:
                metadata["error"] = error[:1000]
            else:
                metadata.pop("error", None)
            document.status = status
            document.parse_metadata = metadata
            document.updated_at = datetime.now(timezone.utc)
            await session.commit()
            break

    async def _persist_chunks(
        self, document_id: str, chunks: List[Dict[str, Any]], parse_metadata: Dict[str, Any]
    ) -> None:
        async for session in self.db_session_factory():
            document = await session.get(Document, document_id)
            if document is None:
                raise RuntimeError(f"文档不存在: {document_id}")
            document.parse_metadata = {**parse_metadata, "stage": "indexing"}
            now = datetime.now(timezone.utc)
            session.add_all(
                [
                    Chunk(
                        id=chunk["id"],
                        document_id=document_id,
                        chunk_index=index,
                        content=chunk["content"],
                        content_type=chunk["content_type"],
                        chunk_metadata=chunk["metadata"],
                        version_status="active",
                        created_at=now,
                    )
                    for index, chunk in enumerate(chunks)
                ]
            )
            await session.commit()
            break

    async def _remove_persisted_chunks(self, document_id: str) -> None:
        async for session in self.db_session_factory():
            await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
            await session.commit()
            break

    async def process_document(
        self, file_path: str, project_ids: List[str], description: Optional[str] = None
    ) -> Dict[str, Any]:
        del description
        document_id = str(uuid.uuid4())
        file_type = self._file_type(file_path)
        content_hash = self._content_hash(file_path)
        if self.vector_store is None:
            # Creating the Milvus client performs I/O, so defer it until an actual ingestion run.
            from src.db.milvus_client import milvus_client

            self.vector_store = milvus_client
        vector_ids: List[str] = []
        document_id, existing = await self._create_document(
            document_id, file_path, file_type, content_hash, project_ids
        )
        if existing is not None:
            return existing
        try:
            parser = self.pdf_parser if file_type == "pdf" else self.markdown_parser
            parsed_content = await parser.parse(file_path)
            if parsed_content.get("file_type") != file_type:
                raise ValueError(f"解析器返回的文件类型不匹配: {parsed_content.get('file_type')}")

            cleaned_content = self.cleaner.clean(parsed_content)
            chunks = []
            for chunk_result in self._get_chunker(file_type).chunk(cleaned_content):
                content = chunk_result.content.replace("\x00", "").strip()
                if content:
                    chunks.append(
                        {
                            "id": str(uuid.uuid4()),
                            "content": content,
                            "content_type": chunk_result.content_type,
                            "metadata": dict(chunk_result.metadata),
                        }
                    )
            if not chunks:
                raise ValueError("文档未生成可索引的文本块")

            await self._set_status(document_id, "processing", "embedding")
            embeddings = self.embedder.embed([chunk["content"] for chunk in chunks])
            if len(embeddings) != len(chunks) or any(not vector for vector in embeddings):
                raise RuntimeError("Embedding 服务未返回完整的非空向量")
            for chunk, dense_vector in zip(chunks, embeddings):
                chunk["dense_vector"] = dense_vector

            parse_metadata = {
                "page_count": len(parsed_content.get("pages", [])),
                "section_count": parsed_content.get("total_sections", 0),
                "block_count": parsed_content.get("total_blocks", 0),
                "chunk_count": len(chunks),
            }
            await self._persist_chunks(document_id, chunks, parse_metadata)

            entities = [
                {
                    "id": chunk["id"],
                    "entity_type": "chunk",
                    "dense_vector": chunk["dense_vector"],
                    "project_ids": project_ids,
                    "version_status": "active",
                    "content_type": chunk["content_type"],
                    "content": chunk["content"],
                    "created_at": int(datetime.now(timezone.utc).timestamp()),
                }
                for chunk in chunks
            ]
            vector_ids = [entity["id"] for entity in entities]
            insert_result = self.vector_store.insert(entities)
            if insert_result.get("insert_count", 0) != len(entities):
                raise RuntimeError("Milvus 未确认全部向量写入")

            await self._set_status(document_id, "ready", "ready")
            logger.info(f"文档处理完成: {file_path}, 生成 {len(chunks)} 个 chunk")
            return {"document_id": document_id, "status": "ready", "chunk_count": len(chunks)}
        except Exception as exc:
            logger.exception(f"文档处理失败: {exc}")
            if vector_ids:
                try:
                    self.vector_store.delete(vector_ids)
                except Exception as cleanup_exc:
                    logger.error(f"Milvus 补偿删除失败: {cleanup_exc}")
            try:
                await self._remove_persisted_chunks(document_id)
            except Exception as cleanup_exc:
                logger.error(f"PostgreSQL chunk 补偿删除失败: {cleanup_exc}")
            await self._set_status(document_id, "failed", "failed", str(exc))
            raise
