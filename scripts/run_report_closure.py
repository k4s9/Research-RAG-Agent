"""One frozen comparison/revision batch; no retries or manual artifact recovery.

prepare is offline; run needs the explicitly approved plan hash. Reopening uses a
separate process and zero inference budget. Semantic review remains separate.
"""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_minimal_live import (
    api_server, configure, frozen_config, record_http, sha, write_json,
)

LIMITS = dict(generation=9, embedding=22, reranker=16, total_tokens=256000)
NO_INFERENCE = dict(generation=0, embedding=0, reranker=0, total_tokens=0)
COMPARISON = (
    "Compare Alpha and Beta under an inclusive 8 GB peak memory limit. "
    "Use exactly three dimensions: method, experimental settings, limitations, with "
    "one row per document per dimension. Locate evidence and read both original documents. "
    "Include dataset, 7B model size, sequence length, batch size and measured peak memory. "
    "Keep quotes short and exact. Recommend only measured configurations. Explicitly retain "
    "the unpaired accuracy caveat and the unresolved energy measurement. Submit a concise report."
)
REVISION = (
    "Revise the saved report: the available memory is now 12 GB inclusive, replacing the "
    "previous 8 GB user limit. Keep both documents and the same three dimensions. "
    "Reassess feasibility only for the measured configurations; do not assume retuning "
    "batch size or sequence length makes Beta fit. Preserve the different-dataset accuracy "
    "caveat and unknown energy difference. Include an explicit change_summary and retain "
    "still-valid source IDs. Submit the complete revised report with short exact quotes."
)


def plan_config(batch_name="report-closure-20260924-v1"):
    previous = frozen_config()  # Check the actual model/provider settings before freezing.
    return dict(
        name=batch_name, limits=LIMITS,
        model=previous["model"], reasoning_effort="low", temperature=0,
        request_timeout_seconds=180, batch_wall_seconds=1500, reopen_timeout_seconds=60,
        comparison_budget=previous["comparison"],
        revision_budget={**previous["comparison"], "model_calls": 3},
        comparison_prompt=COMPARISON, revision_prompt=REVISION,
        tasks=2, retries=0, concurrency=1, probe_requests=0,
        corpus="smoke-v1 synthetic; regression only", vector_backend="memory",
        database="retained isolated PostgreSQL schema; create_all, not migrations",
        enrichment="disabled", cost=None, cost_status="unknown",
        semantic_review="pending", independent_human_score=None,
    )


def prepare(output):
    config = plan_config(output.name)
    output.mkdir(parents=True, exist_ok=False)
    paths = [ROOT / "pyproject.toml", ROOT / "alembic.ini", ROOT / "frontend/app.py"]
    for folder in ("src", "scripts", "tests", "migrations"):
        paths.extend((ROOT / folder).rglob("*.py"))
    paths.extend((ROOT / "eval/corpus/smoke-v1").glob("*.md"))
    hashes = {}
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        target = output / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        hashes[str(relative)] = sha(path)
    write_json(output / "frozen.json", dict(
        config=config, hashes=hashes, python=sys.version,
        commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        git_status=subprocess.check_output(["git", "status", "--short"], text=True),
    ))
    print(json.dumps(dict(plan_sha256=sha(output / "frozen.json"), **config), indent=2))


def require_delivery(run, artifact, request):
    """A terminal label alone does not establish delivery or semantic correctness."""
    from src.core.agent.reports import ResearchReport, validate_report

    if run["status"] not in {"completed", "insufficient_evidence"}:
        raise AssertionError(f"task terminated without delivery: {run['status']}")
    if not artifact or (run.get("state") or {}).get("report_id") != artifact["id"]:
        raise AssertionError("task did not automatically save a report")
    if artifact["run_id"] != run["run_id"] or artifact["session_id"] != request["session_id"]:
        raise AssertionError("report belongs to a different task or session")
    report = ResearchReport.model_validate(artifact["structured"])
    validate_report(report, artifact["evidence"], request["document_ids"])
    if len(report.dimensions) != 3 or len(report.claims) != 6:
        raise AssertionError("expected three dimensions and six comparison rows")
    if not report.incomparable or not report.unresolved:
        raise AssertionError("this corpus requires incomparable conditions and unresolved measurements")
    if run["status"] != "insufficient_evidence":
        raise AssertionError("unresolved measurements must retain insufficient_evidence status")
    if request["task_type"] == "revise" and not report.change_summary.strip():
        raise AssertionError("revision omitted change_summary")
    if artifact["parent_report_id"] != request.get("parent_report_id"):
        raise AssertionError("incorrect parent report")


async def read_artifact(client, report_id):
    if not report_id:
        return None
    response = await client.get(f"/api/v1/chat/reports/{report_id}")
    response.raise_for_status()
    artifact = response.json()
    response = await client.get(f"/api/v1/chat/reports/{report_id}/download")
    response.raise_for_status()
    if response.text != artifact["markdown"]:
        raise AssertionError("download differs from saved Markdown")
    return artifact


async def execute_task(client, request, output):
    output.mkdir()
    write_json(output / "request.json", request)
    started = time.monotonic()
    response = await client.post("/api/v1/chat/runs", json=request)
    response.raise_for_status()
    write_json(output / "submitted.json", response.json())
    run_id = response.json()["run_id"]
    while True:
        response = await client.get(f"/api/v1/chat/runs/{run_id}")
        response.raise_for_status()
        run = response.json()
        write_json(output / "run.json", run)
        if run["status"] not in {"running", "pending", "queued"}:
            break
        if time.monotonic() - started > 630:
            response = await client.post(f"/api/v1/chat/runs/{run_id}/cancel")
            write_json(output / "deadline-cancel.json", response.json())
            raise TimeoutError("task wall deadline exceeded")
        await asyncio.sleep(0.5)
    write_json(output / "timing.json", dict(seconds=time.monotonic() - started))
    artifact = await read_artifact(client, (run.get("state") or {}).get("report_id"))
    if artifact:
        write_json(output / "report.json", artifact)
        (output / "report.md").write_text(artifact["markdown"], encoding="utf-8")
    require_delivery(run, artifact, request)
    return artifact


async def verify_sources(artifact, setup, sessions, output):
    from src.db.models import Chunk, Document

    checks = []
    async with sessions() as db:
        for sid, source in artifact["evidence"].items():
            chunk = await db.get(Chunk, source["chunk_id"])
            document = await db.get(Document, source["document_id"])
            if chunk is None or document is None:
                raise AssertionError(f"missing source {sid}")
            upload = next(u for u in setup["uploads"] if u["document_id"] == document.id)
            original = output / "source/eval/corpus/smoke-v1" / upload["file"]
            content = chunk.content[source["char_start"]:source["char_end"]]
            checks.append(dict(
                source_id=sid, document_match=chunk.document_id == document.id,
                document_hash_match=document.content_hash == source["document_hash"] == sha(original),
                snapshot_matches_database=content == source["content"],
                content_hash_match=hashlib.sha256(content.encode()).hexdigest() == source["content_hash"],
                excerpt_in_original=content in original.read_text(),
            ))
    if not checks or not all(all(v for k, v in c.items() if k != "source_id") for c in checks):
        raise AssertionError("source identity, snapshot or original text mismatch")
    return checks


async def workflow(output, stage, reopen=False):
    import httpx
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from src.api.dependencies import get_document_enricher, get_ingest_pipeline, get_orchestrator
    from src.config.settings import settings
    from src.core.agent.orchestrator import AgentOrchestrator
    from src.core.agent.service import workers
    from src.core.agent.tools import ResearchToolRegistry
    from src.core.ingest.pipeline import DocumentIngestPipeline
    from src.core.retrieval.embedder import Qwen3Embedder
    from src.core.retrieval.hybrid_search import HybridSearch
    from src.core.retrieval.hydration import hydrate_search_results
    from src.db.models import Base
    from src.db.postgres import get_db
    from src.db.vector_store import InMemoryVectorStore
    from src.main import app

    setup_path = output / "setup.json"
    setup = json.loads(setup_path.read_text()) if reopen else dict(
        schema="rag_closure_" + uuid4().hex,
        uploads=[], retained=True, producer_pid=os.getpid(), session_id=str(uuid4()),
    )
    if not re.fullmatch(r"rag_closure_[0-9a-f]{32}", setup["schema"]):
        raise ValueError("invalid isolated schema")
    engine = create_async_engine(settings.postgres_url, poolclass=NullPool,
        connect_args={"timeout": 10}, execution_options={"schema_translate_map": {None: setup["schema"]}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def database():
        async with sessions() as db:
            yield db

    async def hydrate(results, project_ids):
        return await hydrate_search_results(results, project_ids, db_session_factory=database)

    class SkippedEnrichment:
        async def enrich_document(self, *args, **kwargs):
            return {"skipped": "outside this experiment"}

    app.dependency_overrides[get_db] = database
    settings.upload_dir = str(output / "uploads")
    try:
        if not reopen:
            write_json(setup_path, setup)
            async with engine.begin() as conn:
                await conn.execute(text(f'CREATE SCHEMA "{setup["schema"]}"'))
                await conn.run_sync(Base.metadata.create_all)
            vectors = InMemoryVectorStore()
            embedder = Qwen3Embedder()
            searcher = HybridSearch(embedder=embedder, vector_store=vectors)
            registry = ResearchToolRegistry(searcher, hydrator=hydrate, db_session_factory=database)
            pipeline = DocumentIngestPipeline(embedder=embedder, vector_store=vectors, db_session_factory=database)
            app.dependency_overrides.update({
                get_ingest_pipeline: lambda: pipeline,
                get_document_enricher: lambda: SkippedEnrichment(),
                get_orchestrator: lambda: AgentOrchestrator(searcher=searcher, hydrator=hydrate, tool_registry=registry),
            })
        async with api_server(app) as url:
            async with httpx.AsyncClient(base_url=url, timeout=60, trust_env=False) as client:
                if not reopen:
                    response = await client.post("/api/v1/projects", json={"name": "Report closure acceptance"})
                    response.raise_for_status()
                    setup["project_id"] = response.json()["id"]
                    for path in sorted((output / "source/eval/corpus/smoke-v1").glob("*.md")):
                        response = await client.post("/api/v1/documents/upload",
                            files={"file": (path.name, path.read_bytes(), "text/markdown")},
                            data={"project_ids": json.dumps([setup["project_id"]])})
                        response.raise_for_status()
                        item = response.json()
                        setup["uploads"].append(dict(file=path.name, sha256=sha(path), **item))
                        write_json(setup_path, setup)
                        if item["status"] != "ready":
                            raise RuntimeError("upload did not become ready")
                    selected = [u["file"] for u in setup["uploads"] if u["file"] in ("alpha-memory.md", "beta-memory.md")]
                    if len(selected) != 2:
                        raise AssertionError("missing comparison documents")
                    common = dict(session_id=setup["session_id"], project_ids=[setup["project_id"]],
                        document_ids=[u["document_id"] for u in setup["uploads"] if u["file"] in selected], strategy="b2")
                    plan = plan_config(output.name)
                    stage[0] = "comparison"
                    parent = await execute_task(client, dict(**common, task_type="compare",
                        budget=plan["comparison_budget"], message=COMPARISON), output / "comparison")
                    stage[0] = "revision"
                    await execute_task(client, dict(**common, task_type="revise", parent_report_id=parent["id"],
                        budget=plan["revision_budget"], message=REVISION), output / "revision")
                    response = await client.get(f"/api/v1/chat/sessions/{setup['session_id']}")
                    response.raise_for_status()
                    write_json(output / "session.json", response.json())
                else:
                    reopened = {}
                    for name in ("comparison", "revision"):
                        task_dir = output / name
                        original = json.loads((task_dir / "report.json").read_text())
                        artifact = await read_artifact(client, original["id"])
                        if artifact != original or artifact["markdown"] != (task_dir / "report.md").read_text():
                            raise AssertionError("report changed across processes")
                        run = json.loads((task_dir / "run.json").read_text())
                        response = await client.get(f"/api/v1/chat/runs/{run['run_id']}")
                        response.raise_for_status()
                        if response.json() != run:
                            raise AssertionError("run state or budget changed across processes")
                        require_delivery(run, artifact, json.loads((task_dir / "request.json").read_text()))
                        reopened[name] = dict(artifact_identical=True, run_identical=True,
                            sources=await verify_sources(artifact, setup, sessions, output))
                    parent = json.loads((output / "comparison/report.json").read_text())
                    child = json.loads((output / "revision/report.json").read_text())
                    if any(child["evidence"].get(sid) != source for sid, source in parent["evidence"].items()):
                        raise AssertionError("revision changed or renumbered inherited sources")
                    response = await client.get(f"/api/v1/chat/sessions/{setup['session_id']}")
                    response.raise_for_status()
                    if response.json() != json.loads((output / "session.json").read_text()):
                        raise AssertionError("session changed across processes")
                    write_json(output / "reopened.json", dict(
                        automation_passed=True, producer_pid=setup["producer_pid"], reader_pid=os.getpid(),
                        checks=reopened, inherited_sources_unchanged=True, session_identical=True,
                        semantic_review="pending; exact quotes do not establish semantic support",
                        independent_human_score=None,
                    ))
    finally:
        running = list(workers.values())
        for worker in running:
            worker.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        await engine.dispose()
        app.dependency_overrides.clear()


def check_frozen(output):
    frozen = json.loads((output / "frozen.json").read_text())
    if frozen["config"] != plan_config(output.name):
        raise RuntimeError("configuration changed; prepare a new version")
    if any(sha(ROOT / p) != value or sha(output / "source" / p) != value
           for p, value in frozen["hashes"].items()):
        raise RuntimeError("source changed; prepare a new version")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "run", "reopen"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--approved-plan-sha256")
    args = parser.parse_args()
    os.chdir(ROOT)
    configure()
    output = args.output.resolve()
    if args.stage == "prepare":
        prepare(output)
        return
    check_frozen(output)
    if args.stage == "reopen":
        # Also enforce zero calls if an accidental new dependency is introduced later.
        evidence_dir = output / ("reopen-observation-" + uuid4().hex)
        evidence_dir.mkdir(exist_ok=False)
        with record_http(evidence_dir, ["reopen"], limits=NO_INFERENCE):
            asyncio.run(workflow(output, ["reopen"], reopen=True))
        return
    if args.approved_plan_sha256 != sha(output / "frozen.json"):
        parser.error("run requires the explicitly approved frozen plan SHA-256")
    with (output / "started.json").open("x") as stream:
        json.dump(dict(pid=os.getpid(), time=time.time(), plan_sha256=args.approved_plan_sha256), stream)
    stage = ["ingestion"]
    try:
        with record_http(output, stage, limits=LIMITS):
            asyncio.run(asyncio.wait_for(workflow(output, stage), timeout=1500))
        stage[0] = "reopen"
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "reopen", "--output", str(output)],
                       check=True, timeout=60)
    except BaseException as exc:
        # Raw HTTP/run records retain full failures; don't leak endpoint URLs in summaries.
        write_json(output / "failure.json", dict(stage=stage[0], error_type=type(exc).__name__))
        raise


if __name__ == "__main__":
    main()
