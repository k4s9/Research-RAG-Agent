"""One real generation probe, then one comparison; preserve raw HTTP and reopen evidence.

prepare is offline. run consumes the frozen budget once, with no retries. reopen uses
a fresh API process and the retained, isolated PostgreSQL schema; it makes no model calls.
This is a delivery check on synthetic material, not a baseline quality benchmark.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configure():
    config = {**dotenv_values(ROOT / ".env"), **os.environ}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    os.environ.update(
        POSTGRES_URL=config["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1),
        LLM_PROVIDER="openai", LLM_MODEL_NAME=config["LLM_MODEL"],
        EMBEDDING_PROVIDER="remote", EMBEDDING_DIMENSION="2560", EMBEDDING_ENDPOINT="/embeddings",
        RERANKER_PROVIDER="remote", RERANKER_ENDPOINT="/rerank", VECTOR_STORE_BACKEND="memory",
        REQUEST_RETRY_ATTEMPTS="1",
    )


def frozen_config():
    from src.config.settings import settings
    from src.core.agent.runtime import RunBudget

    budget = RunBudget.configured().model_dump()
    expected = dict(model_calls=6, tool_calls=8, active_seconds=600, total_tokens=128000,
                    context_tokens=65536, output_tokens=2048)
    if any(budget[k] != v for k, v in expected.items()):
        raise ValueError("Configured run budget differs from the reviewed minimal experiment")
    if (settings.llm_model_name, settings.llm_reasoning_effort, settings.llm_timeout_seconds,
            settings.agent_search_top_k) != ("Qwen3.8-27B", "low", 180, 3):
        raise ValueError("Inference configuration differs from the reviewed experiment")
    return dict(
        model=settings.llm_model_name, reasoning_effort="low", temperature=0,
        timeout_seconds=180, max_generation_requests=7, max_total_tokens=132096,
        probe=dict(model_calls=1, context_tokens=2048, output_tokens=2048, total_tokens=4096),
        comparison=budget, max_embedding_requests=14, max_reranker_requests=8,
        comparison_runs=1, retries=0, concurrency=1, cost=None, cost_status="unknown",
        corpus="all six existing smoke-v1 synthetic documents; Alpha/Beta selected for comparison",
        database="real PostgreSQL, retained random schema; create_all, not migration validation",
        vector_backend="memory; reopening a report does not establish vector persistence",
        enrichment="disabled", browser_validation=False,
    )


def prepare(output):
    config = frozen_config()
    output.mkdir(parents=True, exist_ok=False)
    files = [*ROOT.joinpath("src").rglob("*.py"), Path(__file__),
             *ROOT.joinpath("eval/corpus/smoke-v1").glob("*.md")]
    hashes = {}
    for path in sorted(files):
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
    print(json.dumps(config, ensure_ascii=False, indent=2))


@contextmanager
def record_http(output, stage):
    """Observe the actual clients' real transports; never replace a service response."""
    import httpx
    import requests
    from src.core.agent.runtime import token_upper_bound

    original_async = httpx.AsyncClient.send
    original_sync = requests.Session.send
    ledger = dict(generation=0, embedding=0, reranker=0, accounted_tokens=0,
                  prompt_tokens=0, completion_tokens=0, unknown_usage_calls=0, cost=None)

    def begin(kind, payload):
        limit = {"generation": 7, "embedding": 14, "reranker": 8}[kind]
        if ledger[kind] >= limit:
            raise RuntimeError(f"batch {kind} request budget exhausted")
        reserve = 0
        if kind == "generation":
            if payload.get("model") != "Qwen3.8-27B" or payload.get("reasoning_effort") != "low":
                raise RuntimeError("Unexpected generation configuration")
            if payload.get("max_tokens") != 2048 or payload.get("temperature") != 0:
                raise RuntimeError("Unexpected generation sampling/output budget")
            context = token_upper_bound([payload["messages"], payload.get("tools")])
            reserve = context + payload["max_tokens"]
            if stage[0] == "probe" and (ledger[kind] or context > 2048):
                raise RuntimeError("probe budget exhausted")
            if ledger["accounted_tokens"] + reserve > 132096:
                raise RuntimeError("batch token budget exhausted")
            ledger["accounted_tokens"] += reserve
            ledger["unknown_usage_calls"] += 1
        ledger[kind] += 1
        entry = dict(kind=kind, stage=stage[0], attempt=ledger[kind], request=payload,
                     reserved_tokens=reserve, status="pending")
        path = output / f"http-{kind}-{ledger[kind]:02d}.json"
        write_json(path, entry)
        write_json(output / "usage.json", ledger)
        return entry, path

    def finish(entry, path, started, response=None, error=None):
        entry["seconds"] = time.monotonic() - started
        entry["status"] = "failed" if error else "returned"
        if error:
            entry["error_type"] = type(error).__name__
        if response is not None:
            entry.update(http_status=response.status_code, raw_response=response.text)
            try:
                body = response.json()
            except ValueError:
                body = {}
            if entry["kind"] == "generation":
                usage = body.get("usage") or {}
                entry["usage"] = usage
                entry["finish_reasons"] = [c.get("finish_reason") for c in body.get("choices", [])]
                if all(type(usage.get(k)) is int and usage[k] >= 0
                       for k in ("prompt_tokens", "completion_tokens")):
                    ledger["unknown_usage_calls"] -= 1
                    for k in ("prompt_tokens", "completion_tokens"):
                        ledger[k] += usage[k]
                    ledger["accounted_tokens"] += (usage["prompt_tokens"] + usage["completion_tokens"]
                                                    - entry["reserved_tokens"])
        write_json(path, entry)
        write_json(output / "usage.json", ledger)
        print(f"{entry['stage']} {entry['kind']} {entry['attempt']}: {entry['status']}, "
              f"{entry['seconds']:.2f}s {entry.get('finish_reasons', '')}", flush=True)

    async def async_send(client, request, *args, **kwargs):
        if not request.url.path.endswith("/chat/completions"):
            return await original_async(client, request, *args, **kwargs)
        entry, path = begin("generation", json.loads(request.content))
        started = time.monotonic()
        response = None
        error = None
        try:
            response = await original_async(client, request, *args, **kwargs)
            await response.aread()
            return response
        except BaseException as exc:
            error = exc
            raise
        finally:
            finish(entry, path, started, response, error)

    def sync_send(client, request, *args, **kwargs):
        endpoint = urlsplit(request.url).path.rstrip("/")
        kind = "embedding" if endpoint.endswith("/embeddings") else "reranker" if endpoint.endswith("/rerank") else None
        if kind is None:
            return original_sync(client, request, *args, **kwargs)
        entry, path = begin(kind, json.loads(request.body))
        started = time.monotonic()
        response = None
        error = None
        try:
            response = original_sync(client, request, *args, **kwargs)
            return response
        except BaseException as exc:
            error = exc
            raise
        finally:
            finish(entry, path, started, response, error)

    httpx.AsyncClient.send = async_send
    requests.Session.send = sync_send
    try:
        yield ledger
    finally:
        httpx.AsyncClient.send = original_async
        requests.Session.send = original_sync


async def probe(output):
    from src.utils.llm_client import LLMClient

    started = time.monotonic()
    result = await LLMClient().acomplete([
        {"role": "user", "content": "Recorded peak memory: Alpha 8 GB; Beta 16 GB. "
         "Under an 8 GB inclusive limit, which one fits? Reply only ALPHA or BETA."}
    ], max_tokens=2048, timeout=180)
    passed = result.get("finish_reason") == "stop" and result["message"].get("content", "").strip() == "ALPHA"
    write_json(output / "probe.json", dict(passed=passed, seconds=time.monotonic()-started, **result))
    if not passed:
        raise RuntimeError("Minimal generation did not return the complete correct answer; comparison not started")


@asynccontextmanager
async def api_server(app):
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            if task.done():
                await task
                raise RuntimeError("API did not start")
            await asyncio.sleep(0.02)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        await task
        sock.close()


async def comparison(output, reopen=False):
    import httpx
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from src.api.dependencies import get_document_enricher, get_ingest_pipeline, get_orchestrator
    from src.config.settings import settings
    from src.core.agent.orchestrator import AgentOrchestrator
    from src.core.agent.reports import ResearchReport, validate_report
    from src.core.agent.runtime import RunBudget
    from src.core.agent.service import workers
    from src.core.agent.tools import ResearchToolRegistry
    from src.core.ingest.pipeline import DocumentIngestPipeline
    from src.core.retrieval.embedder import Qwen3Embedder
    from src.core.retrieval.hybrid_search import HybridSearch
    from src.core.retrieval.hydration import hydrate_search_results
    from src.db.models import Base, Chunk, Document
    from src.db.postgres import get_db
    from src.db.vector_store import InMemoryVectorStore
    from src.main import app

    setup_path = output / "setup.json"
    setup = json.loads(setup_path.read_text()) if reopen else dict(
        schema="rag_minimal_" + uuid4().hex, uploads=[], retained=True, producer_pid=os.getpid())
    if not re.fullmatch(r"rag_minimal_[0-9a-f]{32}", setup["schema"]):
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
                    response = await client.post("/api/v1/projects", json={"name": "Minimal real comparison"})
                    response.raise_for_status()
                    project_id = response.json()["id"]
                    for path in sorted((ROOT / "eval/corpus/smoke-v1").glob("*.md")):
                        response = await client.post("/api/v1/documents/upload",
                            files={"file": (path.name, path.read_bytes(), "text/markdown")},
                            data={"project_ids": json.dumps([project_id])})
                        response.raise_for_status()
                        item = response.json()
                        setup["uploads"].append(dict(file=path.name, sha256=sha(path), **item))
                        write_json(setup_path, setup)
                        if item["status"] != "ready":
                            raise RuntimeError("upload did not become ready")
                    selected = [u["document_id"] for u in setup["uploads"]
                                if u["file"] in ("alpha-memory.md", "beta-memory.md")]
                    request = dict(session_id=str(uuid4()), project_ids=[project_id],
                        document_ids=selected, strategy="b2", task_type="compare",
                        budget=RunBudget.configured().model_dump(),
                        message="Compare Alpha and Beta under an inclusive 8 GB peak memory limit. "
                        "Use three dimensions: method, experimental settings, limitations. "
                        "Locate relevant evidence and read both original documents. Keep the report concise, "
                        "with one row per document per dimension and short exact quotes. Include dataset, "
                        "7B model size, sequence length and batch size, feasibility recommendation, "
                        "unpaired accuracy caveat and unresolved energy measurement. Submit the report.")
                    write_json(output / "task-request.json", request)
                    started = time.monotonic()
                    response = await client.post("/api/v1/chat/runs", json=request)
                    response.raise_for_status()
                    setup["run_id"] = response.json()["run_id"]
                    write_json(setup_path, setup)
                    while True:
                        response = await client.get(f"/api/v1/chat/runs/{setup['run_id']}")
                        response.raise_for_status()
                        run = response.json()
                        write_json(output / "run.json", run)
                        if run["status"] not in ("running", "pending", "queued"):
                            break
                        if time.monotonic() - started > 630:
                            await client.post(f"/api/v1/chat/runs/{setup['run_id']}/cancel")
                            raise RuntimeError("comparison wall deadline exceeded")
                        await asyncio.sleep(0.5)
                    setup["comparison_seconds"] = time.monotonic() - started
                    setup["report_id"] = (run.get("state") or {}).get("report_id")
                    write_json(setup_path, setup)
                    if run["status"] != "completed" or not setup["report_id"]:
                        raise RuntimeError(f"comparison did not deliver: {run['status']}; {run.get('error')}")
                response = await client.get(f"/api/v1/chat/reports/{setup['report_id']}")
                response.raise_for_status()
                artifact = response.json()
                download = await client.get(f"/api/v1/chat/reports/{setup['report_id']}/download")
                download.raise_for_status()
                if download.text != artifact["markdown"]:
                    raise AssertionError("download differs from persisted report")
                if not reopen:
                    write_json(output / "report.json", artifact)
                    (output / "report.md").write_text(download.text, encoding="utf-8")
                else:
                    original = json.loads((output / "report.json").read_text())
                    if artifact != original or download.text != (output / "report.md").read_text():
                        raise AssertionError("fresh API process did not reopen the identical report")
                    request = json.loads((output / "task-request.json").read_text())
                    validate_report(ResearchReport.model_validate(artifact["structured"]),
                                    artifact["evidence"], request["document_ids"])
                    checks = []
                    async with sessions() as db:
                        for sid, evidence in artifact["evidence"].items():
                            chunk = await db.get(Chunk, evidence["chunk_id"])
                            document = await db.get(Document, evidence["document_id"])
                            upload = next(u for u in setup["uploads"] if u["document_id"] == document.id)
                            corpus_path = output / "source/eval/corpus/smoke-v1" / upload["file"]
                            content = chunk.content[evidence["char_start"]:evidence["char_end"]]
                            checks.append(dict(source_id=sid,
                                document_match=chunk.document_id == document.id,
                                document_hash_match=document.content_hash == evidence["document_hash"] == sha(corpus_path),
                                snapshot_matches_database=content == evidence["content"],
                                content_hash_match=hashlib.sha256(content.encode()).hexdigest() == evidence["content_hash"],
                                excerpt_in_original=content in corpus_path.read_text()))
                    if not checks or not all(all(v for k, v in c.items() if k != "source_id") for c in checks):
                        raise AssertionError(f"source checks failed: {checks}")
                    write_json(output / "reopened.json", dict(passed=True, producer_pid=setup["producer_pid"],
                        reader_pid=os.getpid(), artifact_identical=True, download_identical=True,
                        checks=checks, semantic_review="pending; exact quotes are not a semantic judgment"))
                    print(f"Reopened report and checked {len(checks)} persisted sources.", flush=True)
    finally:
        for worker in list(workers.values()):
            worker.cancel()
        if workers:
            await asyncio.gather(*list(workers.values()), return_exceptions=True)
        await engine.dispose()
        app.dependency_overrides.clear()


async def run(output):
    frozen = json.loads((output / "frozen.json").read_text())
    if frozen["config"] != frozen_config() or any(sha(ROOT/p) != h for p, h in frozen["hashes"].items()):
        raise RuntimeError("source/configuration changed since preparation; use a new version")
    # A second invocation cannot silently spend another task/probe budget.
    with (output / "started.json").open("x") as stream:
        json.dump({"pid": os.getpid(), "time": time.time()}, stream)
    stage = ["probe"]
    try:
        with record_http(output, stage):
            await probe(output)
            stage[0] = "comparison"
            await comparison(output)
    except Exception as exc:
        write_json(output / "failure.json", dict(stage=stage[0], error_type=type(exc).__name__, message=str(exc)))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "run", "reopen"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    os.chdir(ROOT)
    configure()
    output = args.output.resolve()
    if args.stage == "prepare":
        prepare(output)
    elif args.stage == "run":
        asyncio.run(run(output))
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "reopen", "--output", str(output)], check=True)
    else:
        asyncio.run(comparison(output, reopen=True))


if __name__ == "__main__":
    main()
