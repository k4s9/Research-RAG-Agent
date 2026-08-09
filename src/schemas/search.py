from pydantic import BaseModel, Field
from typing import Any, List


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
    filename: str = ""
    locator: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    score: float
    project_ids: List[str]
    created_at: str = ""
    retrieval: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    results: List[SearchResultItem]
    total: int
    query_time_ms: int
