from typing import List, Dict, Any, Optional
import uuid
from datetime import datetime
from pathlib import Path
from loguru import logger

from src.core.ingest.pdf_parser import PDFParser
from src.core.ingest.markdown_parser import EnhancedMarkdownParser
from src.core.ingest.cleaner import DocumentCleaner
from src.core.ingest.chunker import DocumentChunker
from src.core.ingest.markdown_chunker import MarkdownChunker
from src.core.retrieval.embedder import Qwen3Embedder
from src.db.milvus_client import milvus_client
from src.db.postgres import get_db as get_db_session
from src.db.models import Document, Chunk
from src.config.settings import settings


class DocumentIngestPipeline:
    def __init__(self, chunk_size: int = 384, overlap: int = 128):
        self.pdf_parser = PDFParser()
        self.md_parser = EnhancedMarkdownParser()
        self.cleaner = DocumentCleaner()
        self.default_chunker = DocumentChunker(chunk_size=chunk_size, overlap=overlap)
        self.md_chunker = MarkdownChunker(chunk_size=chunk_size, overlap=overlap)
        self.embedder = Qwen3Embedder()

    def _get_chunker(self, file_type: str):
        if file_type == "markdown":
            return self.md_chunker
        return self.default_chunker

    async def process_document(self, file_path: str, project_ids: List[str], description: Optional[str] = None) -> Dict[str, Any]:
        try:
            file_ext = Path(file_path).suffix.lower()

            if file_ext == '.pdf':
                parsed_content = await self.pdf_parser.parse(file_path)
            elif file_ext == '.md' or file_ext == '.markdown':
                parsed_content = await self.md_parser.parse(file_path)
            else:
                raise ValueError(f"不支持的文件类型: {file_ext}")

            cleaned_content = self.cleaner.clean(parsed_content)

            chunker = self._get_chunker(file_ext)
            chunk_results = chunker.chunk(cleaned_content)

            chunks = [
                {
                    "content": c.content,
                    "content_type": c.content_type,
                    "metadata": c.metadata
                }
                for c in chunk_results
            ]

            document_id = str(uuid.uuid4())
            file_type_str = file_ext[1:] if file_ext.startswith('.') else file_ext

            async for session in get_db_session():
                document = Document(
                    id=document_id,
                    filename=Path(file_path).name,
                    file_type=file_type_str,
                    file_path=file_path,
                    status="processing",
                    parse_metadata={
                        "page_count": len(parsed_content.get("pages", [])),
                        "section_count": parsed_content.get("total_sections", 0),
                        "block_count": parsed_content.get("total_blocks", 0)
                    },
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                session.add(document)
                await session.commit()
                break

            milvus_entities = []
            for i, chunk in enumerate(chunks):
                chunk_id = str(uuid.uuid4())

                embeddings = self.embedder.embed([chunk["content"]])
                dense_vector = embeddings[0] if embeddings else []

                async for session in get_db_session():
                    content = chunk["content"].replace('\x00', '')
                    content = ''.join(c for c in content if ord(c) <= 0x10FFFF)

                    chunk_record = Chunk(
                        id=chunk_id,
                        document_id=document_id,
                        chunk_index=i,
                        content=content,
                        content_type=chunk.get("content_type", "text"),
                        chunk_metadata=chunk.get("metadata", {}),
                        version_status="active",
                        created_at=datetime.utcnow()
                    )
                    session.add(chunk_record)
                    await session.commit()
                    break

                milvus_entity = {
                    "id": chunk_id,
                    "entity_type": "chunk",
                    "dense_vector": dense_vector,
                    "project_ids": project_ids,
                    "version_status": "active",
                    "content_type": chunk.get("content_type", "text"),
                    "content": chunk.get("content", ""),
                    "created_at": int(datetime.utcnow().timestamp())
                }
                milvus_entities.append(milvus_entity)

            if milvus_entities:
                milvus_client.insert(milvus_entities)

            async for session in get_db_session():
                document = await session.get(Document, document_id)
                document.status = "ready"
                document.updated_at = datetime.utcnow()
                await session.commit()
                break

            logger.info(f"文档处理完成: {file_path}, 生成 {len(chunks)} 个 chunk")
            return {
                "document_id": document_id,
                "status": "ready",
                "chunk_count": len(chunks)
            }

        except Exception as e:
            logger.error(f"文档处理失败: {str(e)}")
            if 'document_id' in locals():
                async for session in get_db_session():
                    document = await session.get(Document, document_id)
                    if document:
                        document.status = "failed"
                        document.updated_at = datetime.utcnow()
                        await session.commit()
                    break
            raise
