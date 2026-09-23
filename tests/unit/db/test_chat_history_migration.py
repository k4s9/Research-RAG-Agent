"""Exercise real Alembic upgrades and history API reads on a legacy database."""

import os
import sqlite3
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from src.core.agent.history import load_recent_history
from src.db.models import Base, ChatSession, Conversation
from src.db.postgres import get_db
from src.main import app

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[3]

# Deliberately frozen old schema: using current Base.create_all would skip the
# ALTER/backfill paths that this regression needs to verify.
LEGACY_SCHEMA = """
CREATE TABLE project (id VARCHAR(36) PRIMARY KEY, name VARCHAR(255) NOT NULL,
 description TEXT, created_at DATETIME, updated_at DATETIME);
CREATE TABLE conversation (id VARCHAR(36) PRIMARY KEY, session_id VARCHAR(36) NOT NULL,
 role VARCHAR(20) NOT NULL, content TEXT NOT NULL, timestamp DATETIME);
CREATE TABLE project_conversation (project_id VARCHAR(36) REFERENCES project(id),
 conversation_id VARCHAR(36) REFERENCES conversation(id),
 PRIMARY KEY(project_id, conversation_id));
CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY);
INSERT INTO alembic_version VALUES ('20260806_01');
INSERT INTO project (id, name) VALUES ('p1', 'legacy project');
INSERT INTO conversation VALUES ('c3', 'legacy', 'user', 'third', '2026-01-02');
INSERT INTO conversation VALUES ('c2', 'legacy', 'assistant', 'second', '2026-01-01');
INSERT INTO conversation VALUES ('c1', 'legacy', 'user', 'first', '2026-01-01');
INSERT INTO project_conversation VALUES ('p1', 'c1');
"""


def migrate(path: Path, target: str = "head") -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=ROOT,
        env={**os.environ, "POSTGRES_URL": f"sqlite+aiosqlite:///{path}"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


class ReadSession:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def get(self, model: type, identifier: str) -> object:
        return self.session.get(model, identifier)

    async def execute(self, statement: object) -> object:
        return self.session.execute(statement)


@pytest.mark.asyncio
@pytest.mark.parametrize("already_upgraded", [False, True])
async def test_legacy_upgrade_restores_session_and_complete_pagination(
    tmp_path: Path,
    already_upgraded: bool,
) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(LEGACY_SCHEMA)
    if already_upgraded:
        migrate(path, "20260918_02")
    migrate(path)
    migrate(path)  # Restarting deployment is safe.
    engine = create_engine(f"sqlite:///{path}")
    factory = sessionmaker(engine, expire_on_commit=False)

    async def database() -> AsyncIterator[ReadSession]:
        with factory() as session:
            yield ReadSession(session)

    app.dependency_overrides[get_db] = database
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            first = await client.get("/api/v1/chat/sessions/legacy?limit=2")
            assert first.status_code == 200, first.text
            page = first.json()
            assert page["project_ids"] == ["p1"]
            assert page["message_count"] == 3
            assert [row["content"] for row in page["messages"]] == ["second", "third"]
            assert page["has_more"] is True
            assert page["next_cursor"] == "1"
            older = await client.get(
                "/api/v1/chat/sessions/legacy",
                params={"limit": 2, "cursor": page["next_cursor"]},
            )
            assert [row["content"] for row in older.json()["messages"]] == ["first"]
            assert older.json()["has_more"] is False
        with factory() as session:
            history = await load_recent_history(ReadSession(session), "legacy")
            assert [row["content"] for row in history] == ["first", "second", "third"]
            assert [row["turn_index"] for row in history] == [0, 1, 2]
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_backfill_preserves_existing_session_and_issued_cursors(tmp_path: Path) -> None:
    path = tmp_path / "mixed.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(LEGACY_SCHEMA)
    migrate(path, "20260918_02")
    engine = create_engine(f"sqlite:///{path}")
    with Session(engine) as session:
        # Seed the old schema without using the current ORM's newly added columns.
        from sqlalchemy import text
        session.execute(text("INSERT INTO chat_session (id, title, project_ids, status, summary_upto_turn) VALUES ('legacy', 'custom title', '[\"p1\"]', 'active', 0)"))
        session.add(
            Conversation(
                id="c4",
                session_id="legacy",
                role="assistant",
                content="current",
                turn_index=0,
            ),
        )
        session.commit()
    engine.dispose()
    migrate(path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as session:
            rows = session.scalars(select(Conversation).order_by(Conversation.turn_index)).all()
            assert [(row.id, row.turn_index) for row in rows] == [
                ("c1", -3),
                ("c2", -2),
                ("c3", -1),
                ("c4", 0),
            ]
            assert session.get(ChatSession, "legacy").title == "custom title"
    finally:
        engine.dispose()


def test_fresh_alembic_upgrade_has_current_columns(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    migrate(path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        inspector = inspect(engine)
        for table in Base.metadata.sorted_tables:
            assert {column["name"] for column in inspector.get_columns(table.name)} == set(
                table.columns.keys(),
            )
    finally:
        engine.dispose()
