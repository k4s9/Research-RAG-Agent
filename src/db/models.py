from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey, JSON, Table
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
import uuid
from datetime import datetime

Base = declarative_base()

# 多对多关系表
project_document = Table(
    'project_document',
    Base.metadata,
    Column('project_id', String(36), ForeignKey('project.id'), primary_key=True),
    Column('document_id', String(36), ForeignKey('document.id'), primary_key=True)
)

project_conversation = Table(
    'project_conversation',
    Base.metadata,
    Column('project_id', String(36), ForeignKey('project.id'), primary_key=True),
    Column('conversation_id', String(36), ForeignKey('conversation.id'), primary_key=True)
)

project_memory = Table(
    'project_memory',
    Base.metadata,
    Column('project_id', String(36), ForeignKey('project.id'), primary_key=True),
    Column('memory_id', String(36), ForeignKey('memory.id'), primary_key=True)
)

class Project(Base):
    __tablename__ = 'project'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关系
    documents = relationship('Document', secondary=project_document, back_populates='projects')
    conversations = relationship('Conversation', secondary=project_conversation, back_populates='projects')
    memories = relationship('Memory', secondary=project_memory, back_populates='projects')

class Document(Base):
    __tablename__ = 'document'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String(255), nullable=False)
    file_type = Column(String(10), nullable=False)  # pdf/pptx/md
    file_path = Column(String(512), nullable=False)
    status = Column(String(20), nullable=False)  # processing/ready/failed
    parse_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关系
    chunks = relationship('Chunk', back_populates='document')
    projects = relationship('Project', secondary=project_document, back_populates='documents')

class Chunk(Base):
    __tablename__ = 'chunk'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey('document.id'), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    content_type = Column(String(20), nullable=False)  # text/table/formula/image_ref
    metadata = Column(JSON, nullable=True)
    version_status = Column(String(20), nullable=False, default='active')  # active/outdated
    superseded_by = Column(String(36), ForeignKey('chunk.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 关系
    document = relationship('Document', back_populates='chunks')
    superseded = relationship('Chunk', remote_side=[id], backref='supersedes')
    version_logs = relationship('VersionLog', back_populates='entity', foreign_keys='VersionLog.entity_id')

class Conversation(Base):
    __tablename__ = 'conversation'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(36), nullable=False)
    role = Column(String(20), nullable=False)  # user/assistant
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    # 关系
    projects = relationship('Project', secondary=project_conversation, back_populates='conversations')
    memories = relationship('Memory', back_populates='source_conversation')

class Memory(Base):
    __tablename__ = 'memory'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    memory_type = Column(String(20), nullable=False)  # milestone/todo/decision/insight
    summary = Column(Text, nullable=False)
    original_context = Column(Text, nullable=False)
    source_conversation_id = Column(String(36), ForeignKey('conversation.id'), nullable=True)
    source_chunk_id = Column(String(36), ForeignKey('chunk.id'), nullable=True)
    version_status = Column(String(20), nullable=False, default='active')  # active/outdated
    superseded_by = Column(String(36), ForeignKey('memory.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)  # for todos
    
    # 关系
    source_conversation = relationship('Conversation', back_populates='memories')
    source_chunk = relationship('Chunk')
    projects = relationship('Project', secondary=project_memory, back_populates='memories')
    superseded = relationship('Memory', remote_side=[id], backref='supersedes')
    version_logs = relationship('VersionLog', back_populates='entity', foreign_keys='VersionLog.entity_id')

class VersionLog(Base):
    __tablename__ = 'version_log'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_id = Column(String(36), nullable=False)
    entity_type = Column(String(20), nullable=False)  # chunk/memory
    action = Column(String(20), nullable=False)  # create/update/outdated/restore
    reason = Column(Text, nullable=True)
    actor_conversation_id = Column(String(36), ForeignKey('conversation.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 关系
    actor_conversation = relationship('Conversation')
