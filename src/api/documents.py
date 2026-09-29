import codecs
import json
import os
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.dependencies import get_document_enricher, get_ingest_pipeline
from src.config.settings import settings
from src.core.document_view import build_document_outline
from src.core.ingest.enrichment import DocumentEnricher
from src.core.ingest.pipeline import DocumentIngestPipeline
from src.core.ingest.pdf_quality import PDFQualityError
from src.db.models import Chunk, Document, IngestBatch, Project
from src.db.postgres import get_db
from src.schemas.document import (
    DocumentDetailResponse,
    DocumentItem,
    DocumentListResponse,
    DocumentStatusResponse,
    DocumentUpdateRequest,
    DocumentUploadResponse,
    IngestBatchItem,
)

router = APIRouter()

ALLOWED_MIME_TYPES = {
    ".pdf": {"application/pdf"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".markdown": {"text/markdown", "text/plain", "application/octet-stream"},
}

DOC_TYPE_PATTERN = "^(paper|report|note|other)$"
DOC_STATUS_PATTERN = "^(processing|ready|failed)$"
MAX_TAGS = 8
MAX_TAG_CHARS = 40
MAX_AUTHORS = 20
MAX_AUTHOR_CHARS = 120


def _write_upload(file: UploadFile, file_path: Path, suffix: str) -> None:
    total_size = 0
    first_block = b""
    utf8_decoder = codecs.getincrementaldecoder("utf-8")() if suffix != ".pdf" else None
    try:
        with file_path.open("wb") as destination:
            while block := file.file.read(1024 * 1024):
                if not first_block:
                    first_block = block
                total_size += len(block)
                if total_size > settings.max_upload_size_bytes:
                    raise HTTPException(status_code=413, detail="文件超过大小限制")
                if utf8_decoder is not None:
                    if b"\x00" in block:
                        raise HTTPException(status_code=400, detail="Markdown 文件包含二进制内容")
                    try:
                        utf8_decoder.decode(block, final=False)
                    except UnicodeDecodeError as exc:
                        raise HTTPException(
                            status_code=400,
                            detail="Markdown 文件必须使用 UTF-8",
                        ) from exc
                destination.write(block)
        if not first_block:
            raise HTTPException(status_code=400, detail="文件内容为空")
        if suffix == ".pdf" and not first_block.startswith(b"%PDF-"):
            raise HTTPException(status_code=400, detail="PDF 文件签名无效")
        if utf8_decoder is not None:
            try:
                utf8_decoder.decode(b"", final=True)
            except UnicodeDecodeError as exc:
                raise HTTPException(status_code=400, detail="Markdown 文件必须使用 UTF-8") from exc
    except Exception:
        file_path.unlink(missing_ok=True)
        try:
            file_path.parent.rmdir()
        except OSError:
            pass
        raise


# 确保上传目录存在
os.makedirs(settings.upload_dir, exist_ok=True)


def _normalize_values(values: list[str], limit: int, max_chars: int) -> list[str]:
    output: list[str] = []
    for value in values:
        cleaned = " ".join(str(value).split())[:max_chars]
        if cleaned and cleaned not in output:
            output.append(cleaned)
        if len(output) >= limit:
            break
    return output


def _document_item(document: Document, chunk_count: int = 0) -> DocumentItem:
    return DocumentItem(
        id=document.id,
        filename=document.filename,
        file_type=document.file_type,
        status=document.status,
        title=document.title,
        doc_type=document.doc_type,
        authors=list(document.authors or []),
        year=document.year,
        venue=document.venue,
        tags=list(document.tags or []),
        summary=document.summary,
        page_count=document.page_count,
        chunk_count=chunk_count,
        ingest_batch_id=document.ingest_batch_id,
        project_ids=[project.id for project in document.projects],
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _batch_item(batch: IngestBatch) -> IngestBatchItem:
    return IngestBatchItem(
        id=batch.id,
        note=batch.note,
        file_count=batch.file_count or 0,
        project_ids=list(batch.project_ids or []),
        created_at=batch.created_at,
    )


async def _chunk_counts(session: AsyncSession, document_ids: list[str]) -> dict[str, int]:
    if not document_ids:
        return {}
    result = await session.execute(
        select(Chunk.document_id, func.count(Chunk.id))
        .where(Chunk.document_id.in_(document_ids))
        .group_by(Chunk.document_id),
    )
    return {document_id: int(count) for document_id, count in result.all()}


async def _load_document(session: AsyncSession, document_id: str) -> Document | None:
    result = await session.execute(
        select(Document).options(selectinload(Document.projects)).where(Document.id == document_id),
    )
    return result.scalar_one_or_none()


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    pipeline: Annotated[DocumentIngestPipeline, Depends(get_ingest_pipeline)],
    enricher: Annotated[DocumentEnricher, Depends(get_document_enricher)],
    session: Annotated[AsyncSession, Depends(get_db)],
    file: Annotated[UploadFile, File()],
    project_ids: str = Form(...),
    description: str | None = Form(None),
    note: str | None = Form(None),
) -> DocumentUploadResponse:
    """上传文档"""
    try:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".pdf", ".md", ".markdown"}:
            raise HTTPException(status_code=400, detail="仅支持 PDF、Markdown 文件")
        declared_type = (file.content_type or "").split(";", 1)[0].lower()
        if declared_type and declared_type not in ALLOWED_MIME_TYPES[suffix]:
            raise HTTPException(status_code=400, detail="文件 MIME 类型与扩展名不匹配")
        try:
            project_ids_list = json.loads(project_ids)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="project_ids 必须是 JSON 数组") from exc
        if not isinstance(project_ids_list, list) or not all(
            isinstance(item, str) for item in project_ids_list
        ):
            raise HTTPException(status_code=400, detail="project_ids 必须是字符串数组")

        safe_name = Path(file.filename).name
        storage_dir = Path(settings.upload_dir) / str(uuid.uuid4())
        storage_dir.mkdir(parents=True, exist_ok=False)
        file_path = storage_dir / safe_name
        _write_upload(file, file_path, suffix)
        logger.info(f"文件上传成功: {safe_name}")

        batch = IngestBatch(
            project_ids=project_ids_list,
            note=note,
            file_count=1,
        )
        session.add(batch)
        await session.commit()
        await session.refresh(batch)

        # 启动文档处理流水线
        result = await pipeline.process_document(
            str(file_path),
            project_ids_list,
            description,
            ingest_batch_id=batch.id,
        )
        if result.get("deduplicated"):
            file_path.unlink(missing_ok=True)
            storage_dir.rmdir()
            batch.file_count = 0
            session.add(batch)
            await session.commit()

        if result.get("status") == "ready" and not result.get("deduplicated"):
            # Summary and tags are derived metadata: enrichment must never
            # block or fail the upload.
            background_tasks.add_task(enricher.enrich_document, result["document_id"])

        return DocumentUploadResponse(
            document_id=result["document_id"],
            status=result["status"],
            message="文档解析与索引已完成",
            ingest_batch_id=batch.id,
            title=result.get("title"),
        )

    except PDFQualityError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "document_id": exc.document_id,
                "page_results": exc.page_results,
            },
        ) from exc
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"文档上传失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"文档上传失败: {str(e)}") from e


@router.get("/{doc_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(
    doc_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentStatusResponse:
    """查询文档解析状态"""
    document = await session.get(Document, doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    metadata = document.parse_metadata or {}
    message = {
        "ready": "文档解析和索引完成",
        "processing": "文档正在处理中",
        "failed": "文档处理失败",
    }.get(document.status, "文档状态未知")
    return DocumentStatusResponse(
        document_id=document.id,
        status=document.status,
        message=message,
        stage=metadata.get("stage"),
        chunk_count=metadata.get("chunk_count"),
        error=metadata.get("error"),
        page_results=metadata.get("page_results", []),
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    session: Annotated[AsyncSession, Depends(get_db)],
    project_id: str | None = None,
    doc_type: str | None = Query(default=None, pattern=DOC_TYPE_PATTERN),
    tag: str | None = None,
    year: int | None = None,
    status: str | None = Query(default=None, pattern=DOC_STATUS_PATTERN),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> DocumentListResponse:
    """文档注册表列表：按 project / doc_type / tag / year / status 过滤 + 分页。"""
    statement = select(Document).options(selectinload(Document.projects))
    if project_id:
        statement = statement.join(Document.projects).where(Project.id == project_id)
    if doc_type:
        statement = statement.where(Document.doc_type == doc_type)
    if year is not None:
        statement = statement.where(Document.year == year)
    if status:
        statement = statement.where(Document.status == status)
    statement = statement.order_by(Document.created_at.desc())
    documents = list((await session.execute(statement)).scalars().unique().all())
    if tag:
        # ``tags`` is a JSON list; containment syntax differs between SQLite
        # and PostgreSQL, so filtering stays in Python for portability.
        documents = [document for document in documents if tag in (document.tags or [])]
    total = len(documents)
    page = documents[offset : offset + limit]
    chunk_counts = await _chunk_counts(session, [document.id for document in page])
    return DocumentListResponse(
        documents=[_document_item(document, chunk_counts.get(document.id, 0)) for document in page],
        total=total,
    )


@router.get("/{doc_id}", response_model=DocumentDetailResponse)
async def get_document(
    doc_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentDetailResponse:
    """文档详情：元数据 + outline + chunk 统计 + ingest_batch。"""
    document = await _load_document(session, doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    chunks = list(
        (
            await session.execute(
                select(Chunk).where(Chunk.document_id == doc_id).order_by(Chunk.chunk_index.asc()),
            )
        )
        .scalars()
        .all(),
    )
    content_type_counts: dict[str, int] = {}
    for chunk in chunks:
        content_type_counts[chunk.content_type] = content_type_counts.get(chunk.content_type, 0) + 1
    batch = (
        await session.get(IngestBatch, document.ingest_batch_id)
        if document.ingest_batch_id
        else None
    )
    item = _document_item(document, chunk_count=len(chunks))
    return DocumentDetailResponse(
        **item.model_dump(),
        outline=build_document_outline(document, chunks),
        ingest_batch=_batch_item(batch) if batch is not None else None,
        content_type_counts=content_type_counts,
    )


@router.patch("/{doc_id}", response_model=DocumentItem)
async def update_document(
    doc_id: str,
    request: DocumentUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentItem:
    """人工订正元数据（该接口不在工具面内，Agent 不能调用）。"""
    document = await _load_document(session, doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    updates: dict[str, Any] = request.model_dump(exclude_unset=True)
    if updates.get("title") is not None:
        document.title = " ".join(str(updates["title"]).split())
    if updates.get("doc_type") is not None:
        document.doc_type = str(updates["doc_type"])
    if updates.get("venue") is not None:
        document.venue = " ".join(str(updates["venue"]).split()) or None
    if "year" in updates:
        document.year = updates["year"]
    if updates.get("authors") is not None:
        document.authors = _normalize_values(updates["authors"], MAX_AUTHORS, MAX_AUTHOR_CHARS)
    if updates.get("tags") is not None:
        document.tags = _normalize_values(updates["tags"], MAX_TAGS, MAX_TAG_CHARS)
    session.add(document)
    await session.commit()
    chunk_counts = await _chunk_counts(session, [document.id])
    return _document_item(document, chunk_counts.get(document.id, 0))
