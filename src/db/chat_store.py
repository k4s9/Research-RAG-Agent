"""Persistence helpers for chat sessions, agent runs and agent steps."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.core.agent.trace import build_result_ref, summarize_tool_result
from src.db.models import AgentRun, AgentStep, ChatSession, Conversation, utc_now

MAX_TITLE_CHARS = 60


class SessionBusy(ValueError):
    pass


def derive_session_title(message: str, max_length: int = MAX_TITLE_CHARS) -> str:
    """Build a session title from the first user message."""
    title = " ".join((message or "").split())
    if not title:
        return "新会话"
    if len(title) <= max_length:
        return title
    return title[: max(max_length - 1, 1)] + "…"


async def get_or_create_session(
    db: AsyncSession,
    session_id: str,
    project_ids: list[str],
    title_hint: str | None = None,
) -> ChatSession:
    """Load the session or create it, keeping old callers working."""
    chat_session = await db.get(ChatSession, session_id)
    scoped_projects = list(project_ids)
    if chat_session is None:
        chat_session = ChatSession(
            id=session_id,
            title=derive_session_title(title_hint) if title_hint else "新会话",
            project_ids=scoped_projects,
            status="active",
            summary_upto_turn=0,
        )
        db.add(chat_session)
        await db.commit()
        await db.refresh(chat_session)
        return chat_session
    if chat_session.active_run_id and list(chat_session.project_ids or []) != scoped_projects:
        raise SessionBusy("未完成任务的项目范围不能修改")
    if scoped_projects and list(chat_session.project_ids or []) != scoped_projects:
        chat_session.project_ids = scoped_projects
        db.add(chat_session)
        await db.commit()
    return chat_session


async def _next_message_index(db: AsyncSession, session_id: str) -> int:
    result = await db.execute(
        select(func.max(Conversation.turn_index)).where(Conversation.session_id == session_id),
    )
    current = result.scalar()
    return 0 if current is None else int(current) + 1


async def append_message(
    db: AsyncSession,
    chat_session: ChatSession,
    role: str,
    content: str,
    *,
    run_id: str | None = None,
    message_metadata: dict[str, Any] | None = None,
) -> Conversation:
    """Append one message to the session log with a stable per-session index."""
    timestamp = utc_now()
    message = Conversation(
        session_id=chat_session.id,
        role=role,
        content=content,
        timestamp=timestamp,
        turn_index=await _next_message_index(db, chat_session.id),
        run_id=run_id,
        message_metadata=message_metadata,
    )
    chat_session.last_active_at = timestamp
    db.add(message)
    db.add(chat_session)
    await db.commit()
    await db.refresh(message)
    return message


async def create_run(
    db: AsyncSession,
    chat_session: ChatSession,
    goal: str,
    run_type: str = "chat",
) -> AgentRun:
    """Create the run row before any model or tool work starts."""
    run = AgentRun(
        id=str(uuid.uuid4()),
        session_id=chat_session.id,
        run_type=run_type,
        goal=goal,
        status="running",
        state={
            "project_ids": list(chat_session.project_ids or []),
            "notes": [],
            "evidence": [],
            "unresolved": [],
        },
        step_count=0,
        max_steps=settings.agent_max_steps,
        started_at=utc_now(),
    )
    claimed = await db.execute(
        update(ChatSession)
        .where(
            ChatSession.id == chat_session.id,
            ChatSession.active_run_id.is_(None),
        )
        .values(active_run_id=run.id)
    )
    if claimed.rowcount != 1:
        raise SessionBusy("会话有未完成任务，请先恢复或取消该任务")
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def finish_run(
    db: AsyncSession,
    run: AgentRun,
    status: str,
    error: str | None = None,
) -> None:
    """Persist the terminal state of a run, including the failure reason."""
    run.status = status
    run.error = error
    run.finished_at = utc_now()
    if status != "waiting_user":
        await db.execute(
            update(ChatSession)
            .where(ChatSession.id == run.session_id, ChatSession.active_run_id == run.id)
            .values(active_run_id=None)
        )
    db.add(run)
    await db.commit()


async def persist_tool_trace(
    db: AsyncSession,
    run: AgentRun,
    trace: list[dict[str, Any]],
) -> int:
    """Write tool_call / observation pairs for every executed tool call."""
    if not trace:
        return 0
    next_index = 0
    added = 0
    for entry in trace:
        tool_name = entry.get("tool")
        arguments = entry.get("arguments")
        error = entry.get("error")
        result = entry.get("result")
        duration_ms = entry.get("duration_ms")
        existing = (
            (
                await db.execute(
                    select(AgentStep).where(
                        AgentStep.run_id == run.id,
                        AgentStep.step_index.in_([next_index, next_index + 1]),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_index = {step.step_index: step for step in existing}
        call_step = by_index.get(next_index) or AgentStep(
            run_id=run.id,
            step_index=next_index,
            step_type="tool_call",
            tool_name=tool_name,
        )
        call_step.arguments = arguments if isinstance(arguments, dict) else None
        db.add(call_step)
        observation = by_index.get(next_index + 1) or AgentStep(
            run_id=run.id,
            step_index=next_index + 1,
            step_type="observation",
            tool_name=tool_name,
        )
        observation.result_summary = summarize_tool_result(result, error)
        observation.result_ref = build_result_ref(tool_name, result)
        observation.error = error
        observation.duration_ms = duration_ms if isinstance(duration_ms, int) else None
        db.add(observation)
        next_index += 2
        added += 2
    run.step_count = next_index
    db.add(run)
    await db.commit()
    return added
