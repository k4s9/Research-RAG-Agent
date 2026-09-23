"""Durable task lifecycle. Deploy one API worker; unfinished work resumes explicitly."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import HTTPException
from sqlalchemy import select, update

from src.config.settings import settings
from src.core.agent.history import load_recent_history
from src.core.agent.runtime import price_usage
from src.core.citations import extract_citation_ids
from src.db.chat_store import (
    SessionBusy,
    _next_message_index,
    append_message,
    create_run,
    get_or_create_session,
    persist_tool_trace,
)
from src.db.models import (
    AgentRun,
    ChatSession,
    Conversation,
    Document,
    Project,
    ReportArtifact,
    utc_now,
)

# Live task handles only. The authoritative state is in PostgreSQL/SQLite.
workers: dict[str, asyncio.Task] = {}


async def prepare_run(db, request):
    if request.document_ids:
        visible = (
            (
                await db.execute(
                    select(Document.id).where(
                        Document.id.in_(request.document_ids),
                        Document.projects.any(Project.id.in_(request.project_ids)),
                    )
                )
            )
            .scalars()
            .all()
        )
        if set(visible) != set(request.document_ids):
            raise HTTPException(404, "选择的材料不在项目范围内")
    parent = None
    if request.parent_report_id:
        parent = await db.get(ReportArtifact, request.parent_report_id)
        if (
            parent is None
            or parent.session_id != request.session_id
            or not set(parent.project_ids).issubset(request.project_ids)
        ):
            raise HTTPException(404, "报告不在当前会话及项目范围内")
    elif request.task_type == "revise":
        parent = (
            (
                await db.execute(
                    select(ReportArtifact)
                    .where(
                        ReportArtifact.session_id == request.session_id,
                    )
                    .order_by(ReportArtifact.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if parent and not set(parent.project_ids).issubset(request.project_ids):
            raise HTTPException(409, "修订须保留原报告项目范围，或显式选择报告")
        if parent is None:
            raise HTTPException(409, "尚无可修订报告")
    try:
        chat = await get_or_create_session(
            db, request.session_id, request.project_ids, request.message
        )
        history = await load_recent_history(db, chat.id, settings.context_recent_turns)
        run = await create_run(
            db,
            chat,
            request.message,
            run_type="chat" if request.task_type == "qa" else request.task_type,
        )
    except SessionBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    run.state = {
        "request": request.model_dump(mode="json"),
        "history": history,
        "parent_report_id": parent.id if parent else None,
        "parent_report": {"report": parent.structured, "evidence": parent.evidence}
        if parent
        else None,
    }
    db.add(run)
    await append_message(db, chat, "user", request.message, run_id=run.id)
    return run


def response_from_state(run, execution):
    evidence = execution.get("evidence", {})
    answer = execution.get("answer", "")
    ids = extract_citation_ids(answer)
    return dict(
        session_id=run.session_id,
        response=answer,
        extracted_memories=[],
        retrieved_context=[],
        citations=[evidence[s] for s in ids if s in evidence],
        invalid_citation_ids=[s for s in ids if s not in evidence],
        tool_trace=execution.get("trace", []),
        evidence=evidence,
        run_id=run.id,
        run_status=execution.get("status", run.status),
        termination_reason=execution.get("reason"),
        usage=price_usage(execution["usage"]) if execution.get("usage") else {},
        report_id=(run.state or {}).get("report_id"),
    )


async def execute_run(db, orchestrator, run, resume_input=None):
    metadata = dict(run.state or {})
    request = metadata["request"]
    execution = metadata.get("execution")

    async def checkpoint(value):
        run.state = {**metadata, "execution": value}
        run.prompt_tokens = value["usage"]["prompt_tokens"]
        run.completion_tokens = value["usage"]["completion_tokens"]
        db.add(run)
        # Both full result snapshots and their readable step summaries commit together.
        await persist_tool_trace(db, run, value.get("trace", []))
        await db.commit()

    # A crash between final checkpoint and artifact transaction needs no new model call.
    if execution and execution.get("status") in {"completed", "insufficient_evidence"}:
        result = response_from_state(run, execution)
        result["report"] = execution.get("report")
    else:
        result = await orchestrator.handle_message(
            request["message"],
            request["project_ids"],
            run.session_id,
            history=metadata.get("history"),
            include_outdated=request["include_outdated"],
            strategy=request["strategy"],
            task_type=request["task_type"],
            budget=request.get("budget"),
            state=execution,
            checkpoint=checkpoint,
            resume_input=resume_input,
            document_ids=request.get("document_ids"),
            parent_report=metadata.get("parent_report"),
        )
        execution = result["checkpoint"]
    status = result["run_status"]
    if execution and execution.get("usage"):
        execution["usage"] = price_usage(execution["usage"])
    # No report, message, or completed state can be written after cancellation is acknowledged.
    await db.refresh(run)
    if run.status == "cancelled":
        status = "cancelled"
    chat = await db.get(ChatSession, run.session_id)
    stored_report = (
        (await db.execute(select(ReportArtifact).where(ReportArtifact.run_id == run.id)))
        .scalars()
        .first()
    )
    # Baselines also produce savable reports. Quality is scored against the same rubric;
    # structured validation is a B2 feature, not an automatic baseline failure.
    if (
        not result.get("report")
        and request["task_type"] != "qa"
        and execution.get("strategy") in {"b0", "b1"}
        and status in {"completed", "insufficient_evidence"}
    ):
        result["report"] = {"title": request["message"][:255], "format": "markdown"}
    if (
        result.get("report")
        and status in {"completed", "insufficient_evidence"}
        and stored_report is None
    ):
        stored_report = ReportArtifact(
            id=str(uuid.uuid4()),
            run_id=run.id,
            session_id=run.session_id,
            parent_report_id=metadata.get("parent_report_id"),
            title=result["report"]["title"],
            markdown=result["response"],
            structured=result["report"],
            evidence=result["evidence"],
            project_ids=request["project_ids"],
        )
        db.add(stored_report)
    # waiting_user gets one visible question per pause; successful finalization is atomic.
    if status in {"completed", "insufficient_evidence", "waiting_user"} and not (
        run.state or {}
    ).get("finalized"):
        db.add(
            Conversation(
                session_id=run.session_id,
                run_id=run.id,
                role="assistant",
                content=result["response"],
                turn_index=await _next_message_index(db, run.session_id),
                message_metadata={
                    "citation_ids": [c["source_id"] for c in result.get("citations", [])],
                    "citations": result.get("citations", []),
                    "invalid_citation_ids": result.get("invalid_citation_ids", []),
                },
            )
        )
    run.status, run.error = status, result.get("termination_reason")
    run.finished_at = None if status == "waiting_user" else utc_now()
    run.state = {
        **metadata,
        "execution": execution,
        "finalized": True,
        "report_id": stored_report.id if stored_report else None,
    }
    chat.last_active_at = utc_now()
    if status != "waiting_user":
        await db.execute(
            update(ChatSession)
            .where(ChatSession.id == chat.id, ChatSession.active_run_id == run.id)
            .values(active_run_id=None)
        )
    db.add(run)
    db.add(chat)
    await db.commit()
    return {
        **result,
        "run_id": run.id,
        "run_status": status,
        "report_id": stored_report.id if stored_report else None,
    }


async def run_in_background(run_id, database, orchestrator, resume_input=None):
    try:
        async for db in database():
            run = await db.get(AgentRun, run_id)
            await execute_run(db, orchestrator, run, resume_input)
            break
    finally:
        workers.pop(run_id, None)


def schedule(run_id, database, orchestrator, resume_input=None):
    if run_id in workers:
        raise HTTPException(409, "任务已在执行")
    task = asyncio.create_task(run_in_background(run_id, database, orchestrator, resume_input))
    workers[run_id] = task
    # Retrieve failures to avoid unhandled-task warnings; state remains resumable in DB.
    task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
