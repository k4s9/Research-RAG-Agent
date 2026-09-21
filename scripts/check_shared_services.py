"""Read-only shared-service probes; never print credentials or response bodies.

This intentionally does not import application Settings: a shared .env may contain
variables the application does not yet accept. Passing these probes does not imply
that the application's configuration or complete RAG workflow is working.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import requests
from dotenv import dotenv_values


def load_env_file(path: str) -> dict[str, str]:
    return {
        **{key: value for key, value in dotenv_values(path).items() if value is not None},
        **os.environ,
    }


def api_root(url: str) -> str:
    base = url.rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def emit(service: str, result: dict) -> None:
    print(json.dumps({"service": service, **result}, ensure_ascii=False), flush=True)


def http_probe(
    service: str,
    url: str,
    key: str,
    timeout: float,
    payload: dict | None = None,
) -> dict:
    started = time.perf_counter()
    endpoint = urlsplit(url)
    result = {"host": endpoint.hostname, "path": endpoint.path, "retry_count": 0}
    if payload is not None:
        result["request_sha256"] = digest(payload)
    try:
        # Internal endpoints must not inherit a machine-wide external proxy.
        with requests.Session() as session:
            session.trust_env = False
            response = session.request(
                "GET" if payload is None else "POST",
                url,
                headers={"Authorization": f"Bearer {key}"} if key else {},
                json=payload,
                timeout=timeout,
                allow_redirects=False,
            )
        result["http_status"] = response.status_code
        result["response_sha256"] = hashlib.sha256(response.content).hexdigest()
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("unexpected HTTP status")
        data = response.json()
        result["fields"] = sorted(data)
        if payload is None:
            models = [item["id"] for item in data["data"]]
            if not models:
                raise ValueError("empty models response")
            result["models"] = models
        elif service == "llm":
            message = data["choices"][0]["message"]
            if not isinstance(message.get("content"), str) or not message["content"].strip():
                raise ValueError("empty chat completion")
            result.update(model=data.get("model"), content_chars=len(message["content"]))
        elif service == "embedding":
            rows = data["data"]
            dimensions = sorted({len(item["embedding"]) for item in rows})
            if len(rows) != len(payload["input"]) or len(dimensions) != 1 or not dimensions[0]:
                raise ValueError("invalid embedding shape")
            result.update(model=data.get("model"), dimensions=dimensions, count=len(rows))
        elif service == "reranker":
            rows = data.get("results", data.get("data", []))
            indices = [item["index"] for item in rows]
            if not indices or len(set(indices)) != len(indices):
                raise ValueError("invalid reranker indices")
            if any(
                type(index) is not int or not 0 <= index < len(payload["documents"])
                for index in indices
            ):
                raise ValueError("invalid reranker indices")
            score_fields = sorted(
                {name for item in rows for name in ("score", "relevance_score") if name in item},
            )
            if not score_fields:
                raise ValueError("missing reranker scores")
            result.update(model=data.get("model"), indices=indices, score_fields=score_fields)
        result["status"] = "ok"
    except Exception as exc:
        # Exception strings and response bodies can contain connection credentials.
        result.update(status="unavailable_or_invalid", error_type=type(exc).__name__)
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


async def postgres_probe(url: str, timeout: float) -> dict:
    started = time.perf_counter()
    connection = None
    result = {"host": urlsplit(url).hostname, "mode": "read_only"}
    try:
        import asyncpg

        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
        connection = await asyncpg.connect(
            url,
            timeout=timeout,
            command_timeout=timeout,
            server_settings={"default_transaction_read_only": "on"},
        )
        async with connection.transaction(readonly=True):
            version = await connection.fetchval("SHOW server_version")
            assert await connection.fetchval("SELECT 1") == 1
        result.update(status="ok", version=version)
    except Exception as exc:
        result.update(status="unavailable_or_invalid", error_type=type(exc).__name__)
    finally:
        if connection is not None:
            await connection.close(timeout=timeout)
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--skip-concurrency", action="store_true")
    args = parser.parse_args()
    config = load_env_file(args.env_file)
    llm = api_root(config.get("LLM_BASE_URL", "http://133.133.135.63:8000"))
    embedding = api_root(config.get("EMBEDDING_BASE_URL", "http://133.133.135.63:8001"))
    reranker = api_root(config.get("RERANKER_BASE_URL", "http://133.133.135.62:8001"))
    endpoints = [
        (
            "llm",
            llm,
            config.get("LLM_API_KEY", ""),
            {
                "model": config.get("LLM_MODEL", config.get("LLM_MODEL_NAME", "Qwen3.8-27B")),
                "messages": [{"role": "user", "content": "Reply only with OK."}],
                "temperature": 0,
                "max_tokens": 128,
            },
        ),
        (
            "embedding",
            embedding,
            config.get("EMBEDDING_API_KEY", ""),
            {
                "model": config.get("EMBEDDING_MODEL", "Qwen3-Embedding-4B"),
                "input": ["verified calibration evidence", "unrelated weather report"],
            },
        ),
        (
            "reranker",
            reranker,
            config.get("RERANKER_API_KEY", ""),
            {
                "model": config.get("RERANKER_MODEL", "Qwen/Qwen3-Reranker-4B"),
                "query": "verified calibration",
                "documents": ["unrelated weather report", "verified calibration evidence"],
                "top_k": 2,
            },
        ),
    ]
    failed = False
    paths = {"llm": "/chat/completions", "embedding": "/embeddings", "reranker": "/rerank"}
    for name, base, key, payload in endpoints:
        result = http_probe(name, base + paths[name], key, args.timeout, payload)
        emit(name, result)
        failed |= result["status"] != "ok"
        if not args.skip_concurrency and result["status"] == "ok":
            for concurrency in (1, 2, 4, 8):
                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    futures = [
                        executor.submit(http_probe, name, base + "/models", key, args.timeout)
                        for _ in range(concurrency)
                    ]
                    results = [future.result() for future in futures]
                emit(name, {"probe": "models", "concurrency": concurrency, "results": results})
                failed |= any(item["status"] != "ok" for item in results)

    database_url = config.get("DATABASE_URL") or config.get("POSTGRES_URL")
    if database_url:
        result = asyncio.run(postgres_probe(database_url, args.timeout))
    else:
        result = {"status": "unconfigured"}
    emit("postgres", result)
    failed |= result["status"] != "ok"
    if database_url and result["status"] == "ok" and not args.skip_concurrency:

        async def postgres_batch(count: int) -> list[dict]:
            return await asyncio.gather(
                *(postgres_probe(database_url, args.timeout) for _ in range(count)),
            )

        for concurrency in (1, 2, 4, 8):
            results = asyncio.run(postgres_batch(concurrency))
            emit("postgres", {"concurrency": concurrency, "results": results})
            failed |= any(item["status"] != "ok" for item in results)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
