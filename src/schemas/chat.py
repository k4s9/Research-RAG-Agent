from pydantic import BaseModel, Field
from typing import Any, List


class ChatMessageRequest(BaseModel):
    session_id: str
    project_ids: List[str]
    message: str
    include_outdated: bool


class ExtractedMemory(BaseModel):
    memory_id: str
    type: str
    summary: str
    outdated_references: List[str]


class RetrievedContext(BaseModel):
    chunk_id: str
    content_preview: str
    source: str
    filename: str = ""
    locator: dict[str, Any] = Field(default_factory=dict)
    relevance_score: float


class ChatMessageResponse(BaseModel):
    session_id: str
    response: str
    extracted_memories: List[ExtractedMemory]
    retrieved_context: List[RetrievedContext]
    citations: List[dict[str, Any]] = Field(default_factory=list)
    invalid_citation_ids: List[str] = Field(default_factory=list)


class ChatMessageItem(BaseModel):
    role: str
    content: str
    timestamp: str


class ChatSessionResponse(BaseModel):
    session_id: str
    messages: List[ChatMessageItem]
