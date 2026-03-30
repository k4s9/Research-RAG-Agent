from pydantic import BaseModel
from typing import List, Optional

class VersionHistoryItem(BaseModel):
    version: int
    action: str
    summary: str
    reason: Optional[str] = None
    created_at: str

class VersionHistoryResponse(BaseModel):
    entity_id: str
    entity_type: str
    current_status: str
    history: List[VersionHistoryItem]
