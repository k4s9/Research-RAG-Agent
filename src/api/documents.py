import codecs
import json
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
import os
from pathlib import Path
from typing import Optional
from loguru import logger

from src.schemas.document import DocumentUploadResponse, DocumentStatusResponse
from src.core.ingest.pipeline import DocumentIngestPipeline
from src.config.settings import settings
from src.db.models import Document
from src.db.postgres import get_db

router = APIRouter()

ALLOWED_MIME_TYPES = {
    ".pdf": {"application/pdf"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".markdown": {"text/markdown", "text/plain", "application/octet-stream"},
}


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
                            status_code=400, detail="Markdown 文件必须使用 UTF-8"
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


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    project_ids: str = Form(...),
    description: Optional[str] = Form(None),
):
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

        # 启动文档处理流水线
        pipeline = DocumentIngestPipeline()
        result = await pipeline.process_document(str(file_path), project_ids_list, description)
        if result.get("deduplicated"):
            file_path.unlink(missing_ok=True)
            storage_dir.rmdir()

        return DocumentUploadResponse(
            document_id=result["document_id"],
            status=result["status"],
            message="文档已提交解析，请通过 /documents/{doc_id}/status 查询进度",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"文档上传失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"文档上传失败: {str(e)}")


@router.get("/{doc_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(doc_id: str):
    """查询文档解析状态"""
    async for session in get_db():
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
        )
