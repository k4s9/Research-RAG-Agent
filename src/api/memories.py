from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import Memory, Project, VersionLog, utc_now
from src.db.postgres import get_db
from src.schemas.memory import (
    MemoryCreateRequest,
    MemoryCreateResponse,
    MemoryItem,
    MemoryListResponse,
    MemoryStatusUpdateRequest,
    MemoryStatusUpdateResponse,
)

router = APIRouter()

@router.get("", response_model=MemoryListResponse)
async def get_memories(
    memory_type: str | None = None,
    project_id: str | None = None,
    version_status: str | None = Query(default=None, pattern="^(active|outdated|resolved)$"),
    session: AsyncSession = Depends(get_db),
) -> MemoryListResponse:
    """List memories with project and lifecycle filters."""
    statement = select(Memory).options(selectinload(Memory.projects)).order_by(Memory.created_at.desc())
    if memory_type:
        statement = statement.where(Memory.memory_type == memory_type)
    if version_status:
        statement = statement.where(Memory.version_status == version_status)
    if project_id:
        statement = statement.join(Memory.projects).where(Project.id == project_id)
    result = await session.execute(statement)
    memories = list(result.scalars().unique().all())
    return MemoryListResponse(memories=[_item(memory) for memory in memories], total=len(memories))


def _item(memory: Memory) -> MemoryItem:
    return MemoryItem(
        id=memory.id,
        memory_type=memory.memory_type,
        summary=memory.summary,
        version_status=memory.version_status,
        project_ids=[project.id for project in memory.projects],
        original_context=memory.original_context,
        source_conversation_id=memory.source_conversation_id,
        source_chunk_id=memory.source_chunk_id,
        resolved_at=memory.resolved_at.isoformat() if memory.resolved_at else None,
        created_at=memory.created_at,
    )


@router.post("", response_model=MemoryCreateResponse, status_code=201)
async def create_memory(
    request: MemoryCreateRequest,
    session: AsyncSession = Depends(get_db),
) -> MemoryCreateResponse:
    projects = []
    if request.project_ids:
        result = await session.execute(select(Project).where(Project.id.in_(request.project_ids)))
        projects = list(result.scalars().all())
        if len(projects) != len(set(request.project_ids)):
            raise HTTPException(status_code=404, detail="One or more projects do not exist")
    memory = Memory(
        memory_type=request.memory_type,
        summary=request.summary.strip(),
        original_context=request.original_context,
        source_conversation_id=request.source_conversation_id,
        source_chunk_id=request.source_chunk_id,
        projects=projects,
    )
    session.add(memory)
    await session.commit()
    return MemoryCreateResponse(**_item(memory).model_dump())

@router.patch("/{memory_id}/status", response_model=MemoryStatusUpdateResponse)
async def update_memory_status(
    memory_id: str,
    request: MemoryStatusUpdateRequest,
    session: AsyncSession = Depends(get_db),
) -> MemoryStatusUpdateResponse:
    """Apply a lifecycle transition and retain an audit log."""
    memory = await session.get(Memory, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    if memory.version_status == request.version_status:
        return MemoryStatusUpdateResponse(
            memory_id=memory_id, version_status=memory.version_status, message="状态未变化"
        )
    memory.version_status = request.version_status
    memory.resolved_at = utc_now() if request.version_status == "resolved" else None
    session.add(VersionLog(
        entity_id=memory.id,
        entity_type="memory",
        action="outdated" if request.version_status == "outdated" else "update",
        reason=request.reason,
    ))
    await session.commit()
    return MemoryStatusUpdateResponse(
        memory_id=memory_id,
        version_status=memory.version_status,
        message="记忆状态更新成功",
    )
