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
    run_id: str | None = None
    run_status: str | None = None


class ChatMessageItem(BaseModel):
    role: str
    content: str
    timestamp: str
    message_id: str | None = None
    turn_index: int | None = None
    run_id: str | None = None
    token_count: int | None = None


class ChatSessionResponse(BaseModel):
    session_id: str
    messages: list[ChatMessageItem]
    title: str | None = None
    status: str | None = None
    project_ids: list[str] = Field(default_factory=list)
    rolling_summary: str | None = None
    summary_upto_turn: int = 0
    created_at: str | None = None
    last_active_at: str | None = None
    message_count: int = 0
    next_cursor: str | None = None
    has_more: bool = False


class ChatSessionCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    project_ids: list[str] = Field(default_factory=list)


class ChatSessionItem(BaseModel):
    session_id: str
    title: str
    status: str
    project_ids: list[str] = Field(default_factory=list)
    message_count: int = 0
    created_at: str | None = None
    last_active_at: str | None = None


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionItem]
    total: int


class AgentStepItem(BaseModel):
    step_index: int
    step_type: str
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    result_summary: str | None = None
    result_ref: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    created_at: str | None = None


class AgentRunResponse(BaseModel):
    run_id: str
    session_id: str
    run_type: str
    goal: str
    status: str
    plan: Any = None
    state: dict[str, Any] | None = None
    step_count: int = 0
    max_steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    steps: list[AgentStepItem] = Field(default_factory=list)


class AgentRunItem(BaseModel):
    run_id: str
    session_id: str
    run_type: str
    goal: str
    status: str
    step_count: int = 0
    max_steps: int = 0
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str | None = None


class AgentRunListResponse(BaseModel):
    runs: list[AgentRunItem]
    total: int
