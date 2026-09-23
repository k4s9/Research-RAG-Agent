import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_orchestrator
from src.config.settings import settings
from src.core.agent.orchestrator import AgentOrchestrator
from src.db.chat_store import (
    append_message,
)
from src.db.models import AgentRun, AgentStep, ChatSession, Conversation, ReportArtifact, utc_now
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
    ResumeRequest,
)

router = APIRouter()

MAX_PAGE_SIZE = 200
DEFAULT_MESSAGE_PAGE_SIZE = 50


@router.get("/runtime")
async def runtime_identity():
    import hashlib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    return {
        "model": settings.llm_model_name,
        "provider": settings.llm_provider,
        "vector_backend": settings.vector_store_backend,
        "embedding_model": settings.embedding_model,
        "embedding_provider": settings.embedding_provider,
        "embedding_dimension": settings.embedding_dimension,
        "reranker_model": settings.reranker_model,
        "reranker_provider": settings.reranker_provider,
        "temperature": 0,
        "reasoning_effort": settings.llm_reasoning_effort,
        "llm_timeout_seconds": settings.llm_timeout_seconds,
        "agent_search_top_k": settings.agent_search_top_k,
        "code_hashes": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((root / "src").rglob("*.py"))
        },
    }


@router.post("/runs", status_code=202)
async def submit_run(
    request: ChatMessageRequest,
    http_request: Request,
    orchestrator: Annotated[AgentOrchestrator, Depends(get_orchestrator)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from src.core.agent.service import prepare_run, schedule

    run = await prepare_run(db, request)
    database = http_request.app.dependency_overrides.get(get_db, get_db)
    schedule(run.id, database, orchestrator)
    return {"run_id": run.id, "status": run.status, "session_id": run.session_id}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    from src.core.agent.service import workers

    run = await db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(404, "任务不存在")
    if run.status in {"completed", "insufficient_evidence", "budget_exceeded", "cancelled"}:
        return {"run_id": run.id, "status": run.status}
    task = workers.get(run_id)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await db.refresh(run)
    run.status, run.error, run.finished_at = "cancelled", "user_cancelled", utc_now()
    await db.execute(
        update(ChatSession).where(ChatSession.active_run_id == run.id).values(active_run_id=None)
    )
    db.add(run)
    await db.commit()
    return {"run_id": run.id, "status": run.status}


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume_run(
    run_id: str,
    request: ResumeRequest,
    http_request: Request,
    orchestrator: Annotated[AgentOrchestrator, Depends(get_orchestrator)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from src.core.agent.service import schedule, workers

    run = await db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(404, "任务不存在")
    if run.id in workers:
        raise HTTPException(409, "任务已在执行")
    if run.status in {"completed", "insufficient_evidence"}:
        return {
            "run_id": run.id,
            "status": run.status,
            "report_id": (run.state or {}).get("report_id"),
        }
    if run.status in {"cancelled", "budget_exceeded"}:
        raise HTTPException(409, "任务已终止，不能通过恢复重置预算")
    if not (run.state or {}).get("request"):
        raise HTTPException(409, "旧任务没有恢复所需 checkpoint")
    if run.status == "waiting_user" and not (request.message or "").strip():
        raise HTTPException(422, "请提供澄清回复")
    chat = await db.get(ChatSession, run.session_id)
    claimed = await db.execute(
        update(ChatSession)
        .where(
            ChatSession.id == chat.id,
            (ChatSession.active_run_id.is_(None)) | (ChatSession.active_run_id == run.id),
        )
        .values(active_run_id=run.id)
    )
    if claimed.rowcount != 1:
        raise HTTPException(409, "会话正在执行另一任务")
    if request.message:
        await append_message(db, chat, "user", request.message, run_id=run.id)
    run.state = {**run.state, "finalized": False}
    run.status, run.error, run.finished_at = "running", None, None
    db.add(run)
    await db.commit()
    schedule(
        run.id,
        http_request.app.dependency_overrides.get(get_db, get_db),
        orchestrator,
        request.message,
    )
    return {"run_id": run.id, "status": run.status}


@router.get("/reports/{report_id}")
async def get_report(report_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    report = await db.get(ReportArtifact, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    return {
        key: getattr(report, key)
        for key in (
            "id",
            "run_id",
            "session_id",
            "parent_report_id",
            "title",
            "markdown",
            "structured",
            "evidence",
            "project_ids",
            "created_at",
        )
    }


@router.get("/reports/{report_id}/download")
async def download_report(report_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    report = await db.get(ReportArtifact, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    return Response(
        report.markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="report-{report.id}.md"'},
    )


@router.get("/runs/{run_id}/results/{operation_id}")
async def get_tool_result(
    run_id: str, operation_id: str, db: Annotated[AsyncSession, Depends(get_db)]
):
    run = await db.get(AgentRun, run_id)
    for entry in (run.state or {}).get("execution", {}).get("trace", []) if run else []:
        if entry.get("operation_id") == operation_id:
            return entry
    raise HTTPException(404, "工具结果不存在")


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
    from src.core.agent.service import execute_run, prepare_run, workers

    run = await prepare_run(db, request)
    workers[run.id] = asyncio.current_task()
    try:
        result = await execute_run(db, orchestrator, run)
    finally:
        workers.pop(run.id, None)
    if result["run_status"] == "failed":
        raise HTTPException(
            500,
            {"run_id": run.id, "run_status": "failed", "reason": result.get("termination_reason")},
        )
    return ChatMessageResponse(
        **{k: v for k, v in result.items() if k in ChatMessageResponse.model_fields}
    )


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
