from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base
from src.db.postgres import get_db
from src.main import app

pytestmark = pytest.mark.unit


@pytest.fixture
async def version_client(tmp_path: Path) -> AsyncIterator[tuple]:
    engine = create_engine(f"sqlite:///{tmp_path / 'versions.db'}")
    sessions = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    class SessionAdapter:
        def __init__(self, session: object) -> None:
            self.session = session

        def add(self, instance: object) -> None:
            self.session.add(instance)

        async def get(self, model: type, identifier: str) -> object:
            return self.session.get(model, identifier)

        async def execute(self, statement: object) -> object:
            return self.session.execute(statement)

        async def commit(self) -> None:
            self.session.commit()

        async def refresh(self, instance: object) -> None:
            self.session.refresh(instance)

    async def database() -> AsyncIterator[SessionAdapter]:
        with sessions() as session:
            yield SessionAdapter(session)

    app.dependency_overrides[get_db] = database
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, sessions
    app.dependency_overrides.clear()
    engine.dispose()


async def create_memory(client: httpx.AsyncClient, summary: str) -> str:
    project = await client.post("/api/v1/projects", json={"name": f"Project {summary}"})
    created = await client.post(
        "/api/v1/memories",
        json={
            "memory_type": "decision",
            "summary": summary,
            "original_context": "context",
            "project_ids": [project.json()["id"]],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


@pytest.mark.asyncio
async def test_version_history_reads_real_version_log(version_client: tuple) -> None:
    client, _ = version_client
    memory_id = await create_memory(client, "使用 Flash Attention 方案")

    outdated = await client.patch(
        f"/api/v1/memories/{memory_id}/status",
        json={"version_status": "outdated", "reason": "被新方案取代"},
    )
    assert outdated.status_code == 200, outdated.text
    restored = await client.patch(
        f"/api/v1/memories/{memory_id}/status",
        json={"version_status": "active", "reason": "复现实验通过后恢复"},
    )
    assert restored.status_code == 200, restored.text

    response = await client.get(f"/api/v1/versions/{memory_id}/history")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entity_id"] == memory_id
    assert body["entity_type"] == "memory"
    assert body["current_status"] == "active"
    assert [item["version"] for item in body["history"]] == [2, 1]
    assert [item["action"] for item in body["history"]] == ["update", "outdated"]
    assert body["history"][0]["reason"] == "复现实验通过后恢复"
    assert body["history"][1]["reason"] == "被新方案取代"
    assert body["history"][0]["summary"] == "使用 Flash Attention 方案"
    assert body["history"][0]["created_at"]

    missing = await client.get("/api/v1/versions/does-not-exist/history")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_version_history_without_logs_returns_current_status(version_client: tuple) -> None:
    client, _ = version_client
    memory_id = await create_memory(client, "尚未变更的结论")

    response = await client.get(f"/api/v1/versions/{memory_id}/history")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["current_status"] == "active"
    assert body["entity_type"] == "memory"
    assert body["history"] == []
