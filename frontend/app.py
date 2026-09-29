"""Single-user research workspace. Task state and artifacts come from the API."""

import json
import mimetypes
import tempfile
import uuid
from pathlib import Path

import gradio as gr
import requests

from src.config.settings import settings

API = settings.api_client_url.rstrip("/") + "/api/v1"
TASK_TYPES = {"材料问答": "qa", "跨文档比较": "compare", "结论核查": "verify", "修订报告": "revise"}
STATUS_NAMES = {
    "running": "执行中",
    "waiting_user": "等待澄清",
    "completed": "已完成",
    "insufficient_evidence": "证据不足",
    "failed": "执行失败",
    "cancelled": "已取消",
    "budget_exceeded": "预算耗尽",
}


def api(method, path, **kwargs):
    response = requests.request(method, API + path, timeout=40, **kwargs)
    if not response.ok:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        message = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
        raise gr.Error(message[:1000])
    return response.json()


def refresh_choices():
    sessions = api("GET", "/chat/sessions")["sessions"]
    projects = api("GET", "/projects")["projects"]
    return (
        gr.update(choices=[(s["title"], s["session_id"]) for s in sessions]),
        gr.update(choices=[(p["name"], p["id"]) for p in projects]),
    )


def load_session(session_id):
    if not session_id:
        return [], gr.update(choices=[], value=None)
    session = api("GET", f"/chat/sessions/{session_id}")
    runs = api("GET", f"/chat/sessions/{session_id}/runs")["runs"]
    return (
        [{"role": m["role"], "content": m["content"]} for m in session["messages"]],
        gr.update(
            choices=[
                (f"{STATUS_NAMES.get(r['status'], r['status'])} · {r['goal'][:50]}", r["run_id"])
                for r in runs
            ],
            value=runs[0]["run_id"] if runs else None,
        ),
    )


def create_session(projects):
    result = api("POST", "/chat/sessions", json={"project_ids": projects or []})
    sessions, _ = refresh_choices()
    sessions["value"] = result["session_id"]
    return sessions


def load_documents(projects):
    documents = {}
    for project in projects or []:
        offset = 0
        while True:
            page = api(
                "GET", "/documents", params={"project_id": project, "limit": 100, "offset": offset}
            )
            documents.update({d["id"]: d for d in page["documents"]})
            offset += len(page["documents"])
            if offset >= page["total"] or not page["documents"]:
                break
    return gr.update(
        choices=[
            (f"{d.get('title') or d['filename']} · {d['filename']}", d["id"])
            for d in documents.values()
            if d["status"] == "ready"
        ],
        value=[],
    )


def submit(message, session_id, projects, documents, kind, parent_id):
    if not message.strip() or not projects:
        raise gr.Error("请选择项目并输入任务。")
    session_id = session_id or str(uuid.uuid4())
    result = api(
        "POST",
        "/chat/runs",
        json={
            "session_id": session_id,
            "project_ids": projects,
            "message": message,
            "document_ids": documents or [],
            "task_type": TASK_TYPES[kind],
            "strategy": "b2",
            "parent_report_id": parent_id.strip() or None,
        },
    )
    choices, _ = refresh_choices()
    choices["value"] = session_id
    return result["run_id"], choices, "任务已提交。"


def refresh_run(run_id):
    if not run_id:
        return "选择或提交任务。", [], {}, "", "", None
    run = api("GET", f"/chat/runs/{run_id}")
    state = run.get("state") or {}
    execution = state.get("execution") or {}
    usage = execution.get("usage") or {}
    status = STATUS_NAMES.get(run["status"], run["status"])
    description = f"**{status}** · 模型请求 {usage.get('model_calls', 0)} · 工具尝试 {usage.get('tool_calls', 0)} · 活跃用时 {usage.get('active_seconds', 0):.1f} 秒"
    if run.get("error"):
        description += "\n\n" + run["error"]
    if execution.get("answer") and run["status"] == "waiting_user":
        description += "\n\n" + execution["answer"]
    steps = [
        [
            t.get("step"),
            t.get("tool"),
            t.get("status"),
            t.get("duration_ms"),
            t.get("error") or ("复用已有结果" if t.get("cached") else "已保存原始结果"),
        ]
        for t in execution.get("trace", [])
    ]
    evidence = execution.get("evidence", {})
    report_id = state.get("report_id") or ""
    markdown = execution.get("answer", "")
    download = None
    if report_id:
        report = api("GET", f"/chat/reports/{report_id}")
        markdown = report["markdown"]
        path = Path(tempfile.gettempdir()) / f"research-report-{report_id}.md"
        path.write_text(markdown, encoding="utf-8")
        download = str(path)
    return description, steps, evidence, markdown, report_id, download


def show_source(sources, source_id):
    source = sources.get(source_id.strip().strip("[]")) if source_id else None
    if not source:
        return "输入报告中的来源编号（例如 S1）以查看原文快照。"
    return f"**{source['filename']}** · {source['locator']}\n\n{source['content']}"


def recover(run_id, reply):
    api("POST", f"/chat/runs/{run_id}/resume", json={"message": reply.strip() or None})
    return "恢复请求已提交。"


def cancel(run_id):
    result = api("POST", f"/chat/runs/{run_id}/cancel")
    return STATUS_NAMES.get(result["status"], result["status"])


def upload(file, projects):
    if not file or not projects:
        raise gr.Error("请选择项目和 PDF/Markdown 文件。")
    path = Path(file)
    with path.open("rb") as stream:
        result = api(
            "POST",
            "/documents/upload",
            files={
                "file": (
                    path.name,
                    stream,
                    mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                )
            },
            data={"project_ids": json.dumps(projects)},
        )
    return f"{path.name}：{result['status']}"


def create_project(name):
    if not name.strip():
        raise gr.Error("请输入项目名称。")
    api("POST", "/projects", json={"name": name.strip(), "description": ""})
    return refresh_choices()[1]


with gr.Blocks(title="科研材料比较与证据核查") as demo:
    gr.Markdown(
        "# 科研材料比较与证据核查\n选择项目与材料，提出比较或核查任务。报告保留来源快照，可继续澄清和修订。"
    )
    with gr.Row():
        sessions = gr.Dropdown(label="会话", choices=[])
        projects = gr.Dropdown(label="项目", choices=[], multiselect=True)
        refresh = gr.Button("刷新列表")
        new_session = gr.Button("新建会话")
    documents = gr.CheckboxGroup(label="本次材料（不选则由任务发现）", choices=[])
    with gr.Tab("任务与报告"):
        kind = gr.Radio(list(TASK_TYPES), label="任务类型", value="跨文档比较")
        message = gr.Textbox(
            label="任务要求",
            lines=3,
            placeholder="比较所选材料的方法、实验设置与局限，判断哪种适合 8GB 显存；每项结论给出处。",
        )
        parent_id = gr.Textbox(label="待修订报告编号（留空使用本会话最新报告）")
        start = gr.Button("开始任务", variant="primary")
        run_id = gr.Textbox(label="任务编号")
        previous = gr.Dropdown(label="打开历史任务", choices=[])
        status = gr.Markdown("选择或提交任务。")
        with gr.Row():
            refresh_task = gr.Button("刷新任务")
            cancel_task = gr.Button("取消任务")
        steps = gr.Dataframe(headers=["序号", "操作", "状态", "耗时 ms", "结果"], interactive=False)
        reply = gr.Textbox(label="澄清回复", placeholder="填写需要补充的硬件或材料约束")
        resume = gr.Button("回复 / 恢复中断任务")
        report = gr.Markdown()
        report_id = gr.Textbox(label="报告编号", interactive=False)
        download = gr.File(label="下载 Markdown 报告", interactive=False)
        with gr.Accordion("证据原文", open=False):
            evidence = gr.JSON(label="来源与位置")
            source_id = gr.Textbox(label="来源编号", placeholder="S1")
            source_text = gr.Markdown()
    with gr.Tab("会话记录"):
        history = gr.Chatbot(label="已保存的对话")
        reload_history = gr.Button("刷新会话记录")
    with gr.Tab("添加材料"):
        project_name = gr.Textbox(label="新项目名称")
        new_project = gr.Button("创建项目")
        file = gr.File(label="PDF / Markdown", file_types=[".pdf", ".md", ".markdown"])
        upload_button = gr.Button("上传到所选项目")
        upload_status = gr.Textbox(label="摄入结果")

    refresh.click(refresh_choices, outputs=[sessions, projects])
    demo.load(refresh_choices, outputs=[sessions, projects])
    projects.change(load_documents, inputs=projects, outputs=documents)
    sessions.change(load_session, inputs=sessions, outputs=[history, previous])
    new_session.click(create_session, inputs=projects, outputs=sessions)
    new_project.click(create_project, inputs=project_name, outputs=projects)
    reload_history.click(load_session, inputs=sessions, outputs=[history, previous])
    start.click(
        submit,
        inputs=[message, sessions, projects, documents, kind, parent_id],
        outputs=[run_id, sessions, status],
    )
    previous.change(lambda value: value or "", inputs=previous, outputs=run_id)
    outputs = [status, steps, evidence, report, report_id, download]
    refresh_task.click(refresh_run, inputs=run_id, outputs=outputs)
    run_id.change(refresh_run, inputs=run_id, outputs=outputs)
    gr.Timer(2).tick(refresh_run, inputs=run_id, outputs=outputs)
    source_id.change(show_source, inputs=[evidence, source_id], outputs=source_text)
    resume.click(recover, inputs=[run_id, reply], outputs=status)
    cancel_task.click(cancel, inputs=run_id, outputs=status)
    upload_button.click(upload, inputs=[file, projects], outputs=upload_status)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=settings.gradio_port, share=False)
