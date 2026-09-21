"""Load recent conversation turns from the database.

The orchestrator stays storage-agnostic: the API layer loads the last N turns
from ``conversation`` and injects them through the ``history`` argument.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Conversation

DEFAULT_HISTORY_TURNS = 6


async def load_recent_history(
    db: AsyncSession,
    session_id: str,
    max_turns: int = DEFAULT_HISTORY_TURNS,
) -> list[dict[str, object]]:
    """Return the last ``max_turns`` turns in chronological order.

    ``conversation.turn_index`` is the 0-based message position inside the
    session, so one turn is a user message plus its assistant reply.
    """
    if max_turns <= 0:
        return []
    statement = (
        select(Conversation)
        .where(Conversation.session_id == session_id)
        .order_by(func.coalesce(Conversation.turn_index, -1).desc())
        .limit(max_turns * 2)
    )
    result = await db.execute(statement)
    messages = list(result.scalars().all())
    messages.reverse()
    return [
        {
            "role": message.role,
            "content": message.content,
            "message_id": message.id,
            "turn_index": message.turn_index,
        }
        for message in messages
    ]
