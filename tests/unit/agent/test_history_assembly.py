from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.agent.history import load_recent_history
from src.db.models import Base, ChatSession, Conversation

pytestmark = pytest.mark.unit


class SessionAdapter:
    """Minimal AsyncSession-shaped adapter over a sync SQLite session."""

    def __init__(self, session: object) -> None:
        self.session = session

    def add(self, instance: object) -> None:
        self.session.add(instance)

    async def execute(self, statement: object) -> object:
        return self.session.execute(statement)

    async def commit(self) -> None:
        self.session.commit()


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker]:
    engine = create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    factory = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    yield factory
    engine.dispose()


def seed_turns(factory: sessionmaker, session_id: str, turns: int) -> None:
    with factory() as session:
        session.add(
            ChatSession(
                id=session_id,
                title="history",
                project_ids=[],
                status="active",
                summary_upto_turn=0,
            ),
        )
        for turn in range(turns):
            for role in ("user", "assistant"):
                session.add(
                    Conversation(
                        id=f"{session_id}-{turn}-{role}",
                        session_id=session_id,
                        role=role,
                        content=f"turn-{turn}-{role}",
                        turn_index=turn * 2 + (0 if role == "user" else 1),
                    ),
                )
        session.commit()


@pytest.mark.asyncio
async def test_history_returns_the_last_turns_in_chronological_order(
    sessions: sessionmaker,
) -> None:
    seed_turns(sessions, "session-1", turns=5)
    with sessions() as session:
        history = await load_recent_history(SessionAdapter(session), "session-1", max_turns=2)

    assert [item["content"] for item in history] == [
        "turn-3-user",
        "turn-3-assistant",
        "turn-4-user",
        "turn-4-assistant",
    ]
    assert [item["role"] for item in history] == ["user", "assistant", "user", "assistant"]
    assert [item["turn_index"] for item in history] == [6, 7, 8, 9]


@pytest.mark.asyncio
async def test_history_is_scoped_to_one_session_and_handles_unknown_sessions(
    sessions: sessionmaker,
) -> None:
    seed_turns(sessions, "session-1", turns=1)
    seed_turns(sessions, "session-2", turns=3)
    with sessions() as session:
        adapter = SessionAdapter(session)
        other = await load_recent_history(adapter, "session-2", max_turns=6)
        missing = await load_recent_history(adapter, "missing-session", max_turns=6)
        disabled = await load_recent_history(adapter, "session-1", max_turns=0)

    assert [item["content"] for item in other] == [
        "turn-0-user",
        "turn-0-assistant",
        "turn-1-user",
        "turn-1-assistant",
        "turn-2-user",
        "turn-2-assistant",
    ]
    assert missing == []
    assert disabled == []


@pytest.mark.asyncio
async def test_history_returns_all_turns_when_the_session_is_shorter_than_the_window(
    sessions: sessionmaker,
) -> None:
    seed_turns(sessions, "session-1", turns=2)
    with sessions() as session:
        history = await load_recent_history(SessionAdapter(session), "session-1", max_turns=6)
    assert len(history) == 4
    assert history[0]["content"] == "turn-0-user"
    assert history[-1]["content"] == "turn-1-assistant"
