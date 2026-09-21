"""Opt-in PostgreSQL regression, isolated in a rolled-back temporary schema."""

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from src.core.agent.tools import ResearchToolRegistry
from src.db.models import Base, Document, Project

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("TEST_POSTGRES_URL"),
        reason="set TEST_POSTGRES_URL to an isolated postgresql+asyncpg test database",
    ),
]


@pytest.mark.asyncio
async def test_document_registry_json_fields_and_multi_project_scope() -> None:
    url = os.environ["TEST_POSTGRES_URL"]
    assert url.startswith("postgresql+asyncpg://"), "TEST_POSTGRES_URL must use asyncpg"
    engine = create_async_engine(url, poolclass=NullPool, connect_args={"timeout": 5})
    schema = f"ab_test_{uuid4().hex}"
    try:
        async with engine.connect() as raw_connection, raw_connection.begin():
            await raw_connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection = await raw_connection.execution_options(schema_translate_map={None: schema})
            await connection.run_sync(Base.metadata.create_all)
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                first, second = Project(id="p1", name="first"), Project(id="p2", name="second")
                session.add_all([first, second])
                session.add(
                    Document(
                        id="shared",
                        filename="paper.pdf",
                        file_type="pdf",
                        file_path="unused",
                        status="ready",
                        doc_type="paper",
                        projects=[first, second],
                        parse_metadata={"outline": []},
                        tags=["verified"],
                        authors=["A"],
                        extra_metadata={"test": True},
                    ),
                )
                await session.flush()

                async def database() -> AsyncIterator[AsyncSession]:
                    yield session

                tools = ResearchToolRegistry(searcher=object(), db_session_factory=database)
                result = await tools.execute(
                    "list_documents",
                    {"tag": "verified"},
                    project_ids=["p1", "p2"],
                    session_id="test",
                )
                assert result["total_matched"] == 1
                assert result["documents"][0]["id"] == "shared"
                assert result["documents"][0]["authors"] == ["A"]
                empty = await tools.execute(
                    "list_documents",
                    {},
                    project_ids=["outside"],
                    session_id="test",
                )
                assert empty["documents"] == []
            # Includes schema creation: no application tables or data are kept.
            await raw_connection.rollback()
    finally:
        await engine.dispose()
