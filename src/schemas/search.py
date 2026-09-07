from typing import Any

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    project_ids: list[str]
    cross_project: bool = False
    include_outdated: bool = False
    content_types: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=100)
    time_decay_enabled: bool = False


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
    project_ids: list[str]
    created_at: str = ""
    retrieval: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    total: int
    query_time_ms: int
