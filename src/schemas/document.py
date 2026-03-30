from pydantic import BaseModel
from typing import Optional, Dict, Any

class DocumentUploadResponse(BaseModel):
    document_id: str
    status: str
    message: str

class DocumentStatusResponse(BaseModel):
    document_id: str
    status: str
    message: str
