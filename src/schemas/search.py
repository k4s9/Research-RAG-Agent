from pydantic import BaseModel
from typing import List, Optional

class SearchRequest(BaseModel):
    query: str
    project_ids: List[str]
    cross_project: bool
    include_outdated: bool
    content_types: List[str]
    top_k: int
    time_decay_enabled: bool

class SearchResultItem(BaseModel):
    id: str
    entity_type: str
    content: str
    content_type: str
    version_status: str
    source: str
    score: float
    project_ids: List[str]
    created_at: str

class SearchResponse(BaseModel):
    results: List[SearchResultItem]
    total: int
    query_time_ms: int
