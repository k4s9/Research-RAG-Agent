from pydantic import BaseModel
from typing import List, Optional

class ProjectCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None

class ProjectResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    created_at: str
    updated_at: str

class ProjectListResponse(BaseModel):
    projects: List[ProjectResponse]
    total: int
