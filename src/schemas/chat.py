from pydantic import BaseModel
from typing import List, Optional

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
    relevance_score: float

class ChatMessageResponse(BaseModel):
    session_id: str
    response: str
    extracted_memories: List[ExtractedMemory]
    retrieved_context: List[RetrievedContext]

class ChatMessageItem(BaseModel):
    role: str
    content: str
    timestamp: str

class ChatSessionResponse(BaseModel):
    session_id: str
    messages: List[ChatMessageItem]
