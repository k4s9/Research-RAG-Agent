from datetime import datetime

from pydantic import BaseModel, Field


class DocumentUploadResponse(BaseModel):
    document_id: str
    status: str
    message: str
    ingest_batch_id: str | None = None
    title: str | None = None


class DocumentStatusResponse(BaseModel):
    document_id: str
    status: str
    message: str
    stage: str | None = None
    chunk_count: int | None = None
    error: str | None = None


class DocumentItem(BaseModel):
    id: str
    filename: str
    file_type: str
    status: str
    title: str | None = None
    doc_type: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    tags: list[str] = Field(default_factory=list)
    summary: str | None = None
    page_count: int | None = None
    chunk_count: int = 0
    ingest_batch_id: str | None = None
    project_ids: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentListResponse(BaseModel):
    documents: list[DocumentItem]
    total: int


class IngestBatchItem(BaseModel):
    id: str
    note: str | None = None
    file_count: int = 0
    project_ids: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


class DocumentOutlineEntry(BaseModel):
    title: str
    level: int = 1
    page: int | None = None
    section_path: list[str] = Field(default_factory=list)
    line_start: int | None = None
    line_end: int | None = None


class DocumentDetailResponse(DocumentItem):
    outline: list[DocumentOutlineEntry] = Field(default_factory=list)
    ingest_batch: IngestBatchItem | None = None
    content_type_counts: dict[str, int] = Field(default_factory=dict)


class DocumentUpdateRequest(BaseModel):
    """人工订正元数据；该接口不在工具面里，Agent 不能调用。"""

    title: str | None = Field(default=None, min_length=1, max_length=512)
    doc_type: str | None = Field(default=None, pattern="^(paper|report|note|other)$")
    authors: list[str] | None = Field(default=None, max_length=20)
    year: int | None = Field(default=None, ge=1900, le=2100)
    venue: str | None = Field(default=None, max_length=255)
    tags: list[str] | None = Field(default=None, max_length=8)
