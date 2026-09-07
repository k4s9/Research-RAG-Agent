from datetime import datetime

from pydantic import BaseModel, Field

class MemoryItem(BaseModel):
    id: str
    memory_type: str
    summary: str
    version_status: str
    project_ids: list[str]
    original_context: str = ""
    source_conversation_id: str | None = None
    source_chunk_id: str | None = None
    resolved_at: str | None = None
    created_at: datetime

class MemoryListResponse(BaseModel):
    memories: list[MemoryItem]
    total: int

class MemoryStatusUpdateRequest(BaseModel):
    version_status: str = Field(pattern="^(active|outdated|resolved)$")
    reason: str | None = Field(default=None, max_length=1000)

class MemoryStatusUpdateResponse(BaseModel):
    memory_id: str
    version_status: str
    message: str


class MemoryCreateRequest(BaseModel):
    memory_type: str = Field(pattern="^(milestone|todo|decision|insight)$")
    summary: str = Field(min_length=1, max_length=2000)
    original_context: str = Field(min_length=1)
    project_ids: list[str] = Field(default_factory=list)
    source_conversation_id: str | None = None
    source_chunk_id: str | None = None


class MemoryCreateResponse(MemoryItem):
    pass
