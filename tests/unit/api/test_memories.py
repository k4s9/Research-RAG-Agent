import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, VersionLog
from src.db.postgres import get_db
from src.main import app

pytestmark = pytest.mark.unit


@pytest.fixture
async def memory_client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'memories.db'}")
    sessions = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    class SessionAdapter:
        def __init__(self, session):
            self.session = session

        def add(self, instance):
            self.session.add(instance)

        async def get(self, model, identifier):
            return self.session.get(model, identifier)

        async def execute(self, statement):
            return self.session.execute(statement)

        async def commit(self):
            self.session.commit()

        async def refresh(self, instance):
            self.session.refresh(instance)

    async def database():
        with sessions() as session:
            yield SessionAdapter(session)

    app.dependency_overrides[get_db] = database
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, sessions
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.mark.asyncio
async def test_memory_create_filter_and_lifecycle_audit(memory_client) -> None:
    client, sessions = memory_client
    project = await client.post("/api/v1/projects", json={"name": "Agent Memory"})
    assert project.status_code == 201
    project_id = project.json()["id"]

    created = await client.post(
        "/api/v1/memories",
        json={
            "memory_type": "decision",
            "summary": "Use Flash Attention for the next experiment.",
            "original_context": "The advisor replaced the previous attention implementation.",
            "project_ids": [project_id],
        },
    )
    assert created.status_code == 201, created.text
    memory_id = created.json()["id"]
    assert created.json()["version_status"] == "active"
    assert created.json()["project_ids"] == [project_id]

    listing = await client.get(
        "/api/v1/memories",
        params={"project_id": project_id, "memory_type": "decision", "version_status": "active"},
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1
    assert listing.json()["memories"][0]["id"] == memory_id

    updated = await client.patch(
        f"/api/v1/memories/{memory_id}/status",
        json={"version_status": "outdated", "reason": "Superseded by a newer decision"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version_status"] == "outdated"

    with sessions() as session:
        result = session.execute(select(VersionLog).where(VersionLog.entity_id == memory_id))
        log = result.scalar_one()
        assert log.action == "outdated"
        assert log.reason == "Superseded by a newer decision"


@pytest.mark.asyncio
async def test_memory_rejects_unknown_project_and_invalid_status(memory_client) -> None:
    client, _ = memory_client
    missing_project = await client.post(
        "/api/v1/memories",
        json={
            "memory_type": "todo",
            "summary": "Run the evaluation.",
            "original_context": "Pending work",
            "project_ids": ["missing-project"],
        },
    )
    assert missing_project.status_code == 404

    invalid_status = await client.patch(
        "/api/v1/memories/missing/status", json={"version_status": "deleted"}
    )
    assert invalid_status.status_code == 422
