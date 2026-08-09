from pydantic import BaseModel
from typing import Optional

class DocumentUploadResponse(BaseModel):
    document_id: str
    status: str
    message: str

class DocumentStatusResponse(BaseModel):
    document_id: str
    status: str
    message: str
    stage: Optional[str] = None
    chunk_count: Optional[int] = None
    error: Optional[str] = None
