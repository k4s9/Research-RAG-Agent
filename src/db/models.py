import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, Integer, String, Table, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# 多对多关系表
project_document = Table(
    "project_document",
    Base.metadata,
    Column("project_id", String(36), ForeignKey("project.id"), primary_key=True),
    Column("document_id", String(36), ForeignKey("document.id"), primary_key=True),
)

project_conversation = Table(
    "project_conversation",
    Base.metadata,
    Column("project_id", String(36), ForeignKey("project.id"), primary_key=True),
    Column("conversation_id", String(36), ForeignKey("conversation.id"), primary_key=True),
)

project_memory = Table(
    "project_memory",
    Base.metadata,
    Column("project_id", String(36), ForeignKey("project.id"), primary_key=True),
    Column("memory_id", String(36), ForeignKey("memory.id"), primary_key=True),
)


class Project(Base):
    __tablename__ = "project"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    # 关系
    documents = relationship("Document", secondary=project_document, back_populates="projects")
    conversations = relationship(
        "Conversation",
        secondary=project_conversation,
        back_populates="projects",
    )
    memories = relationship("Memory", secondary=project_memory, back_populates="projects")


class Document(Base):
    __tablename__ = "document"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String(255), nullable=False)
    file_type = Column(String(10), nullable=False)  # pdf/pptx/md
    file_path = Column(String(512), nullable=False)
    content_hash = Column(String(64), nullable=True, unique=True, index=True)
    status = Column(String(20), nullable=False, index=True)  # processing/ready/failed
    parse_metadata = Column(JSON, nullable=True)
    title = Column(String(512), nullable=True)
    doc_type = Column(String(20), nullable=True, index=True)  # paper/report/note/other
    authors = Column(JSON, nullable=True)
    year = Column(Integer, nullable=True, index=True)
    venue = Column(String(255), nullable=True)
    tags = Column(JSON, nullable=True)
    summary = Column(Text, nullable=True)
    page_count = Column(Integer, nullable=True)
    ingest_batch_id = Column(String(36), ForeignKey("ingest_batch.id"), nullable=True, index=True)
    extra_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    # 关系
    chunks = relationship("Chunk", back_populates="document")
    projects = relationship("Project", secondary=project_document, back_populates="documents")


class IngestBatch(Base):
    __tablename__ = "ingest_batch"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(36), nullable=True)
    project_ids = Column(JSON, nullable=False, default=list)
    note = Column(Text, nullable=True)
    file_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utc_now)


class Chunk(Base):
    __tablename__ = "chunk"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("document.id"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    content_type = Column(String(20), nullable=False)  # text/table/formula/image_ref
    chunk_metadata = Column(JSON, nullable=True)
    version_status = Column(String(20), nullable=False, default="active")  # active/outdated
    superseded_by = Column(String(36), ForeignKey("chunk.id"), nullable=True)
    created_at = Column(DateTime, default=utc_now)

    # 关系
    document = relationship("Document", back_populates="chunks")
    superseded = relationship("Chunk", remote_side=lambda: [Chunk.id], backref="supersedes")


class Conversation(Base):
    __tablename__ = "conversation"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(36), nullable=False)
    role = Column(String(20), nullable=False)  # user/assistant
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=utc_now)
    turn_index = Column(Integer, nullable=True)  # 会话内消息顺序，0 起
    run_id = Column(String(36), ForeignKey("agent_run.id"), nullable=True)
    token_count = Column(Integer, nullable=True)
    message_metadata = Column(JSON, nullable=True)

    __table_args__ = (Index("ix_conversation_session_id_turn_index", "session_id", "turn_index"),)

    # 关系
    projects = relationship(
        "Project",
        secondary=project_conversation,
        back_populates="conversations",
    )
    memories = relationship("Memory", back_populates="source_conversation")


class ChatSession(Base):
    __tablename__ = "chat_session"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(255), nullable=False, default="新会话")
    project_ids = Column(JSON, nullable=False, default=list)
    status = Column(String(20), nullable=False, default="active")  # active/archived
    rolling_summary = Column(Text, nullable=True)
    summary_upto_turn = Column(Integer, nullable=False, default=0)
    last_active_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class AgentRun(Base):
    __tablename__ = "agent_run"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(36), ForeignKey("chat_session.id"), nullable=False, index=True)
    run_type = Column(String(20), nullable=False, default="chat")  # chat/qa/research/report/ingest
    goal = Column(Text, nullable=False, default="")
    # pending/running/waiting_user/completed/failed/cancelled
    status = Column(String(20), nullable=False, default="pending")
    plan = Column(JSON, nullable=True)
    state = Column(JSON, nullable=True)
    step_count = Column(Integer, nullable=False, default=0)
    max_steps = Column(Integer, nullable=False, default=12)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class AgentStep(Base):
    __tablename__ = "agent_step"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id = Column(String(36), ForeignKey("agent_run.id"), nullable=False, index=True)
    step_index = Column(Integer, nullable=False)
    # plan/thought/tool_call/observation/reflection/final/error
    step_type = Column(String(20), nullable=False)
    tool_name = Column(String(64), nullable=True)
    arguments = Column(JSON, nullable=True)
    result_summary = Column(Text, nullable=True)  # 回灌模型的部分，≤ 800 字
    result_ref = Column(String(512), nullable=True)
    error = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    model = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=utc_now)


class Memory(Base):
    __tablename__ = "memory"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    memory_type = Column(String(20), nullable=False)  # milestone/todo/decision/insight
    summary = Column(Text, nullable=False)
    original_context = Column(Text, nullable=False)
    source_conversation_id = Column(String(36), ForeignKey("conversation.id"), nullable=True)
    source_chunk_id = Column(String(36), ForeignKey("chunk.id"), nullable=True)
    version_status = Column(String(20), nullable=False, default="active")  # active/outdated
    superseded_by = Column(String(36), ForeignKey("memory.id"), nullable=True)
    created_at = Column(DateTime, default=utc_now)
    resolved_at = Column(DateTime, nullable=True)  # for todos

    # 关系
    source_conversation = relationship("Conversation", back_populates="memories")
    source_chunk = relationship("Chunk")
    projects = relationship("Project", secondary=project_memory, back_populates="memories")
    superseded = relationship("Memory", remote_side=lambda: [Memory.id], backref="supersedes")


class VersionLog(Base):
    __tablename__ = "version_log"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_id = Column(String(36), nullable=False)
    entity_type = Column(String(20), nullable=False)  # chunk/memory
    action = Column(String(20), nullable=False)  # create/update/outdated/restore
    reason = Column(Text, nullable=True)
    actor_conversation_id = Column(String(36), ForeignKey("conversation.id"), nullable=True)
    created_at = Column(DateTime, default=utc_now)

    # 关系
    actor_conversation = relationship("Conversation")
