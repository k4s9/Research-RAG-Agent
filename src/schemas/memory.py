from pydantic import BaseModel
from typing import List, Optional

class MemoryItem(BaseModel):
    id: str
    memory_type: str
    summary: str
    version_status: str
    project_ids: List[str]
    created_at: str

class MemoryListResponse(BaseModel):
    memories: List[MemoryItem]
    total: int

class MemoryStatusUpdateRequest(BaseModel):
    version_status: str

class MemoryStatusUpdateResponse(BaseModel):
    memory_id: str
    version_status: str
    message: str
