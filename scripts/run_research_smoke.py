"""Freeze and execute the reviewed 3-task development smoke on isolated shared services.

Real HTTP API, PostgreSQL, embedding, reranker and generation; memory vector index.
Only the random schema created here is dropped. No deployment changes. Enrichment
is disabled so its separate generation budget cannot leak into the task experiment.
"""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def experiment(output):
    import httpx
    import uvicorn
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from src.api.dependencies import (
        get_document_enricher, get_ingest_pipeline, get_orchestrator, get_searcher,
    )
    from src.config.settings import settings
    from src.core.agent.orchestrator import AgentOrchestrator
    from src.core.agent.runtime import RunBudget
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
    from scripts.evaluate_tasks import freeze, run, sha, tasks_at

    schema = "rag_smoke_" + uuid4().hex
    engine = create_async_engine(
        settings.postgres_url, poolclass=NullPool, connect_args={"timeout": 10},
        execution_options={"schema_translate_map": {None: schema}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def database():
        async with sessions() as db:
            yield db

    async def hydrate(results, project_ids):
        return await hydrate_search_results(results, project_ids, db_session_factory=database)

    class SkippedEnrichment:
        async def enrich_document(self, *args, **kwargs):
            return {"skipped": "separately budgeted; not part of this experiment"}

    vectors = InMemoryVectorStore()
    embedder = Qwen3Embedder()
    searcher = HybridSearch(embedder=embedder, vector_store=vectors)
    registry = ResearchToolRegistry(searcher, hydrator=hydrate, db_session_factory=database)
    pipeline = DocumentIngestPipeline(
        embedder=embedder, vector_store=vectors, db_session_factory=database,
    )
    app.dependency_overrides.update({
        get_db: database,
        get_ingest_pipeline: lambda: pipeline,
        get_searcher: lambda: searcher,
        get_document_enricher: lambda: SkippedEnrichment(),
        get_orchestrator: lambda: AgentOrchestrator(
            searcher=searcher, hydrator=hydrate, tool_registry=registry,
        ),
    })
    settings.upload_dir = str(output / "uploads")
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    api = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server_task = None
    created = False
    setup = {"schema": schema, "schema_removed": False, "uploads": [], "enrichment": "disabled"}
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
            await conn.run_sync(Base.metadata.create_all)
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            if server_task.done():
                await server_task
                raise RuntimeError("API did not start")
            await asyncio.sleep(0.02)
        async with httpx.AsyncClient(base_url=api, timeout=60, trust_env=False) as client:
            response = await client.post("/api/v1/projects", json={"name": "Frozen research smoke"})
            response.raise_for_status()
            project_id = response.json()["id"]
            corpus = sorted((ROOT / "eval/corpus/smoke-v1").glob("*.md"))
            for path in corpus:
                response = await client.post(
                    "/api/v1/documents/upload",
                    files={"file": (path.name, path.read_bytes(), "text/markdown")},
                    data={"project_ids": json.dumps([project_id])},
                )
                response.raise_for_status()
                upload = response.json()
                if upload["status"] != "ready":
                    raise RuntimeError(f"upload not ready: {path.name}")
                setup["uploads"].append({"file": path.name, "sha256": sha(path), **upload})
            dataset = output / "dataset.jsonl"
            tasks = tasks_at(ROOT / "eval/datasets/research-smoke-v1.jsonl")
            for task in tasks:
                task["project_ids"] = [project_id]
            dataset.write_text("\n".join(json.dumps(t) for t in tasks) + "\n")
            identity_response = await client.get("/api/v1/chat/runtime")
            identity_response.raise_for_status()
            identity = identity_response.json()
            if identity["model"] != "Qwen3.8-27B":
                raise ValueError("Model differs from the approved Qwen3.8-27B")
            config = dict(
                dataset=str(dataset), rubric="eval/rubric-v1.json",
                corpus=[str(p.relative_to(ROOT)) for p in corpus],
                model="Qwen3.8-27B", repetitions=1, strategies=["b0", "b1", "b2"],
                split="development-smoke", expected_tasks=3,
                reviewed_by="Codex source/gold inspection; independent human review pending",
                max_model_calls=54, max_total_tokens=1152000,
                run_budget=RunBudget.configured().model_dump(),
                setup_runner_sha256=sha(Path(__file__)),
                conditions=dict(
                    concurrency=1, cache="shared model warm state unknown; run-local read cache",
                    corpus="all six documents visible to every task and baseline",
                    runtime={k: v for k, v in identity.items() if k != "code_hashes"},
                    database="real PostgreSQL; Base.metadata.create_all in random schema",
                    vector_persistence="memory only; no Milvus claim",
                    enrichment="disabled; not evaluated", token_estimation="UTF-8 upper bound",
                    cost="unknown; service token prices not provided",
                    baseline_differences={
                        "b0": f"one hybrid retrieval (top {settings.agent_search_top_k}), one generation, Markdown report",
                        "b1": "bounded existing read-only tool loop; same report requirement",
                        "b2": "research instructions, structured report validation, one gap follow-up",
                    },
                ),
            )
            write_json(output / "config.json", config)
            write_json(output / "environment.json", dict(
                python=sys.version, architecture=platform.machine(), platform=platform.platform(),
                dependencies={p: importlib.metadata.version(p) for p in (
                    "fastapi", "sqlalchemy", "asyncpg", "httpx", "pydantic", "pymilvus", "pytest",
                )},
                git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            ))
            # Preserve the exact source that the real HTTP server loaded.
            for directory in ("src", "scripts", "eval/corpus", "migrations"):
                shutil.copytree(ROOT / directory, output / "source" / directory,
                                ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(ROOT / "eval/rubric-v1.json", output / "source/rubric-v1.json")
            freeze(output / "config.json", output / "frozen.json")
            print("Frozen. Running 9 tasks, at most 54 LLM requests and 1,152,000 tokens.", flush=True)
            await asyncio.to_thread(run, output / "frozen.json", output / "results", api)
            reports = output / "reports"
            reports.mkdir()
            for sample in tasks_at(output / "results/samples.jsonl"):
                for turn in sample["turns"]:
                    report_id = (turn.get("state") or {}).get("report_id")
                    if report_id:
                        response = await client.get(f"/api/v1/chat/reports/{report_id}")
                        response.raise_for_status()
                        artifact = response.json()
                        write_json(reports / f"{report_id}.json", artifact)
                        download = await client.get(f"/api/v1/chat/reports/{report_id}/download")
                        download.raise_for_status()
                        if download.text != artifact["markdown"]:
                            raise AssertionError("report download differs from stored artifact")
                        (reports / f"{report_id}.md").write_text(download.text)
            print(f"Results: {output / 'results/summary.md'}", flush=True)
    finally:
        for worker in list(workers.values()):
            worker.cancel()
        if workers:
            await asyncio.gather(*list(workers.values()), return_exceptions=True)
        server.should_exit = True
        if server_task:
            await server_task
        sock.close()
        try:
            if created:
                async with engine.begin() as conn:
                    await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                async with engine.connect() as conn:
                    setup["schema_removed"] = await conn.scalar(text(
                        "SELECT count(*) = 0 FROM pg_namespace WHERE nspname = :schema"
                    ), {"schema": schema})
        finally:
            write_json(output / "setup.json", setup)
            await engine.dispose()
            app.dependency_overrides.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.chdir(ROOT)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = {**dotenv_values(ROOT / ".env"), **os.environ}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL",
                "EMBEDDING_MODEL", "RERANKER_API_KEY", "RERANKER_BASE_URL", "RERANKER_MODEL"):
        if config.get(key):
            os.environ[key] = config[key]
    os.environ.update(
        POSTGRES_URL=config["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1),
        LLM_PROVIDER="openai", LLM_MODEL_NAME=config["LLM_MODEL"],
        EMBEDDING_PROVIDER="remote", EMBEDDING_DIMENSION="2560", EMBEDDING_ENDPOINT="/embeddings",
        RERANKER_PROVIDER="remote", RERANKER_ENDPOINT="/rerank", VECTOR_STORE_BACKEND="memory",
        REQUEST_RETRY_ATTEMPTS="1",
    )
    asyncio.run(experiment(output))


if __name__ == "__main__":
    main()
