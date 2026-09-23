"""Run the offline suite without loading workspace credentials or live opt-ins."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    work = Path(tempfile.mkdtemp(prefix="rag-offline-"))
    for name in ("src", "tests", "migrations", "scripts"):
        shutil.copytree(root / name, work / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pyproject.toml", "alembic.ini"):
        shutil.copy2(root / name, work / name)
    (work / "source_hashes.json").write_text(json.dumps({
        str(p.relative_to(work)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(work.rglob("*.py"))
    }, indent=2))
    environment = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("RUN_LIVE", "RUN_SHARED", "TEST_POSTGRES"))
    }
    environment.update(
        PYTHONPATH=str(work),
        POSTGRES_URL=f"sqlite+aiosqlite:///{work}/db.sqlite",
        VECTOR_STORE_BACKEND="memory",
        EMBEDDING_PROVIDER="local",
        RERANKER_PROVIDER="local",
        LLM_PROVIDER="local",
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-q",
        f"--junitxml={work}/results.xml",
        *sys.argv[1:],
    ]
    print(f"Offline results: {work}", flush=True)
    return subprocess.call(command, cwd=work, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
