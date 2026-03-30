from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from typing import List, Optional
from src.schemas.document import DocumentUploadResponse, DocumentStatusResponse

router = APIRouter()

@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    project_ids: List[str] = Form(...),
    description: Optional[str] = Form(None)
):
    """上传文档"""
    # 这里将实现文档上传逻辑
    # 暂时返回模拟响应
    return DocumentUploadResponse(
        document_id="doc-uuid-xxx",
        status="processing",
        message="文档已提交解析，请通过 /documents/{doc_id}/status 查询进度"
    )

@router.get("/{doc_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(doc_id: str):
    """查询文档解析状态"""
    # 这里将实现文档状态查询逻辑
    # 暂时返回模拟响应
    return DocumentStatusResponse(
        document_id=doc_id,
        status="ready",
        message="文档解析完成"
    )
