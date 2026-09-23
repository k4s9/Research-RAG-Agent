"""Run live tests in an isolated source snapshot with explicit shared-service settings.

Run outside the sandbox. Credentials stay in the child environment; the shared .env
is never copied. Configuration translation is for testing and does not repair the
application's Settings. The workflow uses real PostgreSQL/models and memory vectors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
PROXIES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
MODEL_KEYS = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL",
    "RERANKER_API_KEY",
    "RERANKER_BASE_URL",
    "RERANKER_MODEL",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--suite", choices=("contracts", "workflow", "seeded", "all"), default="all",
    )
    parser.add_argument(
        "--dimension", type=int, default=2560, help="confirm with service probe first",
    )
    args = parser.parse_args()
    config = {**dotenv_values(args.env_file), **os.environ}
    if not config.get("DATABASE_URL") or not config.get("LLM_MODEL"):
        parser.error("shared configuration requires DATABASE_URL and LLM_MODEL")
    database_url = config["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1)
    if not database_url.startswith("postgresql+asyncpg://"):
        parser.error("DATABASE_URL must be a PostgreSQL DSN")

    workspace = Path(tempfile.mkdtemp(prefix="research-rag-live-tests-"))
    for name in ("src", "tests", "migrations"):
        shutil.copytree(ROOT / name, workspace / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("alembic.ini", "pyproject.toml"):
        shutil.copy2(ROOT / name, workspace / name)
    manifest = {
        str(path.relative_to(workspace)): hashlib.sha256(path.read_bytes()).hexdigest()
        for directory in ("src", "tests", "migrations")
        for path in (workspace / directory).rglob("*.py")
    }
    (workspace / "source_hashes.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    environment = {key: value for key, value in os.environ.items() if key not in PROXIES}
    environment.update({key: config[key] for key in MODEL_KEYS if config.get(key)})
    environment.update(
        POSTGRES_URL=database_url,
        TEST_POSTGRES_URL=database_url,
        LLM_PROVIDER="openai",
        LLM_MODEL_NAME=config["LLM_MODEL"],
        EMBEDDING_PROVIDER="remote",
        EMBEDDING_ENDPOINT="/embeddings",
        EMBEDDING_DIMENSION=str(args.dimension),
        RERANKER_PROVIDER="remote",
        RERANKER_ENDPOINT="/rerank",
        VECTOR_STORE_BACKEND="memory",
        RUN_LIVE_INTEGRATION="1",
        RUN_SHARED_SERVICES_E2E="1",
    )
    secrets = [
        str(value)
        for key, value in config.items()
        if value
        and any(term in key.upper() for term in ("PASSWORD", "TOKEN", "API_KEY", "DATABASE_URL"))
    ]
    secrets.extend([database_url, unquote(urlsplit(database_url).password or "")])

    def redact(value: str) -> str:
        for secret in sorted(set(secrets), key=len, reverse=True):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value

    targets = []
    if args.suite in {"contracts", "all"}:
        targets.extend(
            [
                "tests/integration/test_live_model_contracts.py",
                "tests/integration/test_postgres_document_registry.py",
            ],
        )
    if args.suite in {"workflow", "seeded", "all"}:
        targets.append("tests/integration/test_shared_services_workflow.py")
    if args.suite == "seeded":
        targets.extend(["-k", "seeded"])
    print(f"Snapshot and sanitized results: {workspace}", flush=True)
    print("Real PostgreSQL/models; memory vector index; temporary test schemas only.", flush=True)
    result_path = workspace / "results.xml"
    with (workspace / "pytest.log").open("w", encoding="utf-8") as log:
        with subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                "pytest",
                *targets,
                "-q",
                "-s",
                "--tb=short",
                f"--junitxml={result_path}",
            ],
            cwd=workspace,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        ) as process:
            assert process.stdout is not None
            for line in process.stdout:
                line = redact(line)
                print(line, end="", flush=True)
                log.write(line)
            exit_code = process.wait()
    if result_path.exists():
        result_path.write_text(redact(result_path.read_text(encoding="utf-8")), encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
