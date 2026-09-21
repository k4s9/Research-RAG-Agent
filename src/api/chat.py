from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_orchestrator
from src.config.settings import settings
from src.core.agent.history import load_recent_history
from src.core.agent.orchestrator import AgentOrchestrator
from src.db.chat_store import (
    append_message,
    create_run,
    finish_run,
    get_or_create_session,
    persist_tool_trace,
)
from src.db.models import AgentRun, AgentStep, ChatSession, Conversation
from src.db.postgres import get_db
from src.schemas.chat import (
    AgentRunItem,
    AgentRunListResponse,
    AgentRunResponse,
    AgentStepItem,
    ChatMessageItem,
    ChatMessageRequest,
    ChatMessageResponse,
    ChatSessionCreateRequest,
    ChatSessionItem,
    ChatSessionListResponse,
    ChatSessionResponse,
)

router = APIRouter()

MAX_PAGE_SIZE = 200
DEFAULT_MESSAGE_PAGE_SIZE = 50


def _isoformat(value: object) -> str | None:
    return value.isoformat() if value is not None else None


def _message_item(message: Conversation) -> ChatMessageItem:
    return ChatMessageItem(
        role=message.role,
        content=message.content,
        timestamp=_isoformat(message.timestamp) or "",
        message_id=message.id,
        turn_index=message.turn_index,
        run_id=message.run_id,
        token_count=message.token_count,
    )


def _session_item(chat_session: ChatSession, message_count: int = 0) -> ChatSessionItem:
    return ChatSessionItem(
        session_id=chat_session.id,
        title=chat_session.title,
        status=chat_session.status,
        project_ids=list(chat_session.project_ids or []),
        message_count=message_count,
        created_at=_isoformat(chat_session.created_at),
        last_active_at=_isoformat(chat_session.last_active_at),
    )


def _step_item(step: AgentStep) -> AgentStepItem:
    return AgentStepItem(
        step_index=step.step_index,
        step_type=step.step_type,
        tool_name=step.tool_name,
        arguments=step.arguments if isinstance(step.arguments, dict) else None,
        result_summary=step.result_summary,
        result_ref=step.result_ref,
        error=step.error,
        duration_ms=step.duration_ms,
        created_at=_isoformat(step.created_at),
    )


def _run_item(run: AgentRun) -> AgentRunItem:
    return AgentRunItem(
        run_id=run.id,
        session_id=run.session_id,
        run_type=run.run_type,
        goal=run.goal,
        status=run.status,
        step_count=run.step_count or 0,
        max_steps=run.max_steps or 0,
        error=run.error,
        started_at=_isoformat(run.started_at),
        finished_at=_isoformat(run.finished_at),
        created_at=_isoformat(run.created_at),
    )


async def _message_count(db: AsyncSession, session_id: str) -> int:
    result = await db.execute(
        select(func.count(Conversation.id)).where(Conversation.session_id == session_id),
    )
    return int(result.scalar() or 0)


@router.post("/message", response_model=ChatMessageResponse)
async def send_message(
    request: ChatMessageRequest,
    orchestrator: Annotated[AgentOrchestrator, Depends(get_orchestrator)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatMessageResponse:
    """发送对话消息：会话不存在则自动创建，历史从数据库加载。"""
    run: AgentRun | None = None
    try:
        chat_session = await get_or_create_session(
            db,
            request.session_id,
            request.project_ids,
            title_hint=request.message,
        )
        history = await load_recent_history(db, chat_session.id, settings.context_recent_turns)
        run = await create_run(db, chat_session, goal=request.message)
        await append_message(db, chat_session, "user", request.message, run_id=run.id)

        result = await orchestrator.handle_message(
            message=request.message,
            project_ids=request.project_ids,
            session_id=chat_session.id,
            history=history,
            include_outdated=request.include_outdated,
        )

        await persist_tool_trace(db, run, result.get("tool_trace") or [])
        citations = result.get("citations", [])
        await append_message(
            db,
            chat_session,
            "assistant",
            result.get("response", ""),
            run_id=run.id,
            message_metadata={
                "citation_ids": [citation.get("source_id") for citation in citations],
                "invalid_citation_ids": result.get("invalid_citation_ids", []),
            },
        )
        await finish_run(db, run, status="completed")

        response = ChatMessageResponse(
            session_id=chat_session.id,
            response=result.get("response", ""),
            extracted_memories=result.get("extracted_memories", []),
            retrieved_context=result.get("retrieved_context", []),
            citations=citations,
            invalid_citation_ids=result.get("invalid_citation_ids", []),
            tool_trace=result.get("tool_trace", []),
            run_id=run.id,
            run_status=run.status,
        )
        logger.info(f"聊天消息处理完成: session_id={chat_session.id}, run_id={run.id}")
        return response

    except Exception as e:
        logger.error(f"聊天消息处理失败: {str(e)}")
        if run is not None:
            try:
                await finish_run(db, run, status="failed", error=str(e))
            except Exception as finish_error:
                logger.error(f"写入 run 失败状态时出错: {finish_error}")
        raise HTTPException(status_code=500, detail=f"聊天消息处理失败: {str(e)}") from e


@router.post("/sessions", response_model=ChatSessionItem, status_code=201)
async def create_chat_session(
    request: ChatSessionCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatSessionItem:
    """显式创建会话。"""
    chat_session = ChatSession(
        title=(request.title or "").strip() or "新会话",
        project_ids=list(request.project_ids),
        status="active",
        summary_upto_turn=0,
    )
    db.add(chat_session)
    await db.commit()
    await db.refresh(chat_session)
    return _session_item(chat_session, message_count=0)


@router.get("/sessions", response_model=ChatSessionListResponse)
async def list_chat_sessions(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> ChatSessionListResponse:
    """按最近活跃时间列出会话。"""
    total = int(
        (await db.execute(select(func.count()).select_from(ChatSession))).scalar() or 0,
    )
    counts = (
        select(Conversation.session_id, func.count(Conversation.id).label("message_count"))
        .group_by(Conversation.session_id)
        .subquery()
    )
    statement = (
        select(ChatSession, func.coalesce(counts.c.message_count, 0))
        .outerjoin(counts, counts.c.session_id == ChatSession.id)
        .order_by(func.coalesce(ChatSession.last_active_at, ChatSession.created_at).desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(statement)).all()
    return ChatSessionListResponse(
        sessions=[
            _session_item(chat_session, message_count=int(count)) for chat_session, count in rows
        ],
        total=total,
    )


@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_chat_session(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=DEFAULT_MESSAGE_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None),
) -> ChatSessionResponse:
    """返回真实消息列表，支持按消息序号游标向前翻页。"""
    chat_session = await db.get(ChatSession, session_id)
    if chat_session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    statement = select(Conversation).where(Conversation.session_id == session_id)
    if cursor is not None:
        try:
            cursor_index = int(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="cursor 必须是消息序号") from exc
        statement = statement.where(Conversation.turn_index < cursor_index)
    statement = statement.order_by(func.coalesce(Conversation.turn_index, -1).desc()).limit(
        limit + 1,
    )
    rows = list((await db.execute(statement)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()
    next_cursor = (
        str(rows[0].turn_index) if has_more and rows and rows[0].turn_index is not None else None
    )

    return ChatSessionResponse(
        session_id=chat_session.id,
        messages=[_message_item(message) for message in rows],
        title=chat_session.title,
        status=chat_session.status,
        project_ids=list(chat_session.project_ids or []),
        rolling_summary=chat_session.rolling_summary,
        summary_upto_turn=chat_session.summary_upto_turn or 0,
        created_at=_isoformat(chat_session.created_at),
        last_active_at=_isoformat(chat_session.last_active_at),
        message_count=await _message_count(db, session_id),
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    run_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentRunResponse:
    """返回 run 的每一步工具调用、参数、结果摘要与耗时。"""
    run = await db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    steps = list(
        (
            await db.execute(
                select(AgentStep)
                .where(AgentStep.run_id == run_id)
                .order_by(AgentStep.step_index.asc()),
            )
        )
        .scalars()
        .all(),
    )
    return AgentRunResponse(
        run_id=run.id,
        session_id=run.session_id,
        run_type=run.run_type,
        goal=run.goal,
        status=run.status,
        plan=run.plan,
        state=run.state if isinstance(run.state, dict) else None,
        step_count=run.step_count or 0,
        max_steps=run.max_steps or 0,
        prompt_tokens=run.prompt_tokens or 0,
        completion_tokens=run.completion_tokens or 0,
        error=run.error,
        started_at=_isoformat(run.started_at),
        finished_at=_isoformat(run.finished_at),
        created_at=_isoformat(run.created_at),
        updated_at=_isoformat(run.updated_at),
        steps=[_step_item(step) for step in steps],
    )


@router.get("/sessions/{session_id}/runs", response_model=AgentRunListResponse)
async def list_session_runs(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> AgentRunListResponse:
    """列出会话下的 run。"""
    chat_session = await db.get(ChatSession, session_id)
    if chat_session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    total = int(
        (
            await db.execute(
                select(func.count(AgentRun.id)).where(AgentRun.session_id == session_id),
            )
        ).scalar()
        or 0,
    )
    runs = list(
        (
            await db.execute(
                select(AgentRun)
                .where(AgentRun.session_id == session_id)
                .order_by(AgentRun.created_at.desc())
                .limit(limit)
                .offset(offset),
            )
        )
        .scalars()
        .all(),
    )
    return AgentRunListResponse(runs=[_run_item(run) for run in runs], total=total)
