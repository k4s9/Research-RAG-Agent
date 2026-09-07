from typing import Any

from pydantic import BaseModel, Field


class ChatMessageRequest(BaseModel):
    session_id: str
    project_ids: list[str]
    message: str
    include_outdated: bool = False


class ExtractedMemory(BaseModel):
    memory_id: str
    type: str
    summary: str
    outdated_references: list[str]


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
    extracted_memories: list[ExtractedMemory]
    retrieved_context: list[RetrievedContext]
    citations: list[dict[str, Any]] = Field(default_factory=list)
    invalid_citation_ids: list[str] = Field(default_factory=list)
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)


class ChatMessageItem(BaseModel):
    role: str
    content: str
    timestamp: str


class ChatSessionResponse(BaseModel):
    session_id: str
    messages: list[ChatMessageItem]
