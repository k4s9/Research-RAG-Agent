from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Chunk, Memory, VersionLog
from src.db.postgres import get_db
from src.schemas.version import VersionHistoryItem, VersionHistoryResponse

router = APIRouter()


def _isoformat(value: object) -> str:
    return value.isoformat() if value is not None else ""


@router.get("/{entity_id}/history", response_model=VersionHistoryResponse)
async def get_version_history(
    entity_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> VersionHistoryResponse:
    """读取真实 VersionLog。

    VersionLog 不保存逐版本快照，因此 `summary` 为实体当前文本，历史条目只区分
    action / reason / created_at。
    """
    logs = list(
        (
            await session.execute(
                select(VersionLog)
                .where(VersionLog.entity_id == entity_id)
                .order_by(VersionLog.created_at.asc()),
            )
        )
        .scalars()
        .all(),
    )
    memory = await session.get(Memory, entity_id)
    chunk = None if memory is not None else await session.get(Chunk, entity_id)
    if not logs and memory is None and chunk is None:
        raise HTTPException(status_code=404, detail="未找到该实体的版本历史")

    if memory is not None:
        entity_type = "memory"
        current_status = memory.version_status
        summary = memory.summary
    elif chunk is not None:
        entity_type = "chunk"
        current_status = chunk.version_status
        summary = chunk.content[:500]
    else:
        entity_type = logs[0].entity_type if logs else "unknown"
        current_status = "unknown"
        summary = ""

    history = [
        VersionHistoryItem(
            version=index + 1,
            action=log.action,
            summary=summary,
            reason=log.reason,
            created_at=_isoformat(log.created_at),
        )
        for index, log in enumerate(logs)
    ]
    history.reverse()
    return VersionHistoryResponse(
        entity_id=entity_id,
        entity_type=entity_type,
        current_status=current_status,
        history=history,
    )
