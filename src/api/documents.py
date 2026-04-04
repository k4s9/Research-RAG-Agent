from fastapi import APIRouter, UploadFile, File, Form, HTTPException
import os
from pathlib import Path
from typing import List, Optional
from loguru import logger

from src.schemas.document import DocumentUploadResponse, DocumentStatusResponse
from src.core.ingest.pipeline import DocumentIngestPipeline
from src.config.settings import settings

router = APIRouter()

# 确保上传目录存在
os.makedirs(settings.upload_dir, exist_ok=True)

@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    project_ids: str = Form(...),
    description: Optional[str] = Form(None)
):
    """上传文档"""
    try:
        # 保存上传的文件
        file_path = os.path.join(settings.upload_dir, file.filename)
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        logger.info(f"文件上传成功: {file.filename}")
        
        # 解析 project_ids
        import json
        project_ids_list = json.loads(project_ids)
        
        # 启动文档处理流水线
        pipeline = DocumentIngestPipeline()
        result = await pipeline.process_document(file_path, project_ids_list, description)
        
        return DocumentUploadResponse(
            document_id=result["document_id"],
            status=result["status"],
            message="文档已提交解析，请通过 /documents/{doc_id}/status 查询进度"
        )
        
    except Exception as e:
        logger.error(f"文档上传失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"文档上传失败: {str(e)}")

@router.get("/{doc_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(doc_id: str):
    """查询文档解析状态"""
    # 这里需要从数据库中查询文档状态
    # 暂时返回模拟数据
    return DocumentStatusResponse(
        document_id=doc_id,
        status="ready",
        message="文档解析完成"
    )
