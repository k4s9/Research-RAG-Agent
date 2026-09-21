import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config.settings import settings
from src.core.ingest.enrichment import DocumentEnricher, parse_enrichment_payload
from src.db.models import Base, Chunk, Document
from src.utils.async_llm_client import AsyncLLMClient

pytestmark = pytest.mark.unit


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


class FakeLLM:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **kwargs: object) -> str:
        del kwargs
        self.prompts.append(prompt)
        if isinstance(self.payload, Exception):
            raise self.payload
        return str(self.payload)


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker]:
    engine = create_engine(f"sqlite:///{tmp_path / 'enrichment.db'}")
    factory = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    yield factory
    engine.dispose()


def database(factory: sessionmaker) -> Callable[[], AsyncIterator[SessionAdapter]]:
    async def provider() -> AsyncIterator[SessionAdapter]:
        with factory() as session:
            yield SessionAdapter(session)

    return provider


def seed_ready_document(factory: sessionmaker, document_id: str = "doc-1") -> None:
    with factory() as session:
        session.add(
            Document(
                id=document_id,
                filename="attention.pdf",
                file_type="pdf",
                file_path="/tmp/attention.pdf",
                status="ready",
                title="Attention Is All You Need",
                doc_type="paper",
            ),
        )
        session.add(
            Chunk(
                id="chunk-1",
                document_id=document_id,
                chunk_index=0,
                content="Flash Attention reduces memory usage.",
                content_type="text",
                chunk_metadata={"page_start": 1},
                version_status="active",
            ),
        )
        session.commit()


def test_parse_enrichment_payload_clamps_and_validates() -> None:
    parsed = parse_enrichment_payload(
        '```json\n{"summary": "  摘要  文本 ", "tags": ["a", "a", "b", 7]}\n```',
    )
    assert parsed == {"summary": "摘要 文本", "tags": ["a", "b"]}
    assert parse_enrichment_payload("no json here") is None
    assert parse_enrichment_payload('{"tags": ["a"]}') is None
    assert parse_enrichment_payload('{"summary": ""}') is None


@pytest.mark.asyncio
async def test_enrichment_stores_summary_and_tags(sessions: sessionmaker) -> None:
    seed_ready_document(sessions)
    llm = FakeLLM('{"summary": "论文提出 Flash Attention", "tags": ["attention", "显存"]}')
    enricher = DocumentEnricher(llm_client=llm, db_session_factory=database(sessions))

    result = await enricher.enrich_document("doc-1")

    assert result == {"summary": "论文提出 Flash Attention", "tags": ["attention", "显存"]}
    assert "Flash Attention reduces memory usage." in llm.prompts[0]
    with sessions() as session:
        document = session.get(Document, "doc-1")
        assert document.summary == "论文提出 Flash Attention"
        assert document.tags == ["attention", "显存"]
        assert document.status == "ready"


@pytest.mark.asyncio
async def test_enrichment_failure_keeps_document_ready(sessions: sessionmaker) -> None:
    seed_ready_document(sessions)
    for payload in (RuntimeError("LLM 不可用"), "这不是 JSON"):
        enricher = DocumentEnricher(
            llm_client=FakeLLM(payload),
            db_session_factory=database(sessions),
        )
        assert await enricher.enrich_document("doc-1") is None

    with sessions() as session:
        document = session.get(Document, "doc-1")
        assert document.status == "ready"
        assert document.summary is None
        assert document.tags is None


@pytest.mark.asyncio
async def test_enrichment_skips_documents_that_already_have_a_summary(
    sessions: sessionmaker,
) -> None:
    seed_ready_document(sessions)
    with sessions() as session:
        document = session.get(Document, "doc-1")
        document.summary = "已有摘要"
        document.tags = ["existing"]
        session.commit()

    llm = FakeLLM('{"summary": "新摘要", "tags": ["new"]}')
    enricher = DocumentEnricher(llm_client=llm, db_session_factory=database(sessions))

    result = await enricher.enrich_document("doc-1")

    assert result == {"summary": "已有摘要", "tags": ["existing"], "skipped": True}
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_enrichment_http_wait_allows_other_tasks_and_preserves_manual_tags(
    sessions: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_ready_document(sessions)
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_base_url", "https://model.example/v1")
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert "Flash Attention" in json.loads(request.content)["messages"][0]["content"]
        entered.set()
        await release.wait()
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"summary":"async summary","tags":["generated"]}',
                        },
                    },
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        enricher = DocumentEnricher(
            llm_client=AsyncLLMClient(client),
            db_session_factory=database(sessions),
        )
        task = asyncio.create_task(enricher.enrich_document("doc-1"))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            # Simulate another request while HTTP generation is still pending.
            with sessions() as session:
                document = session.get(Document, "doc-1")
                assert document.status == "ready"
                document.tags = ["manually-corrected"]
                session.commit()
            assert not task.done()
            release.set()
            assert (await task)["summary"] == "async summary"
        finally:
            release.set()
            await task
    with sessions() as session:
        assert session.get(Document, "doc-1").tags == ["manually-corrected"]


@pytest.mark.asyncio
async def test_enrichment_timeout_cancels_http_and_keeps_ready(
    sessions: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_ready_document(sessions)
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "document_enrichment_timeout_seconds", 0.05)
    cancelled = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("unreachable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        enricher = DocumentEnricher(
            llm_client=AsyncLLMClient(client),
            db_session_factory=database(sessions),
        )
        assert await enricher.enrich_document("doc-1") is None
    assert cancelled.is_set()
    with sessions() as session:
        document = session.get(Document, "doc-1")
        assert document.status == "ready"
        assert document.summary is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status, expected_calls", [(429, 2), (503, 2), (401, 1)])
async def test_async_model_retries_only_transient_errors(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected_calls: int,
) -> None:
    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(settings, "request_retry_attempts", 2)
    monkeypatch.setattr(settings, "request_retry_backoff_seconds", 0)
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(status)
        return httpx.Response(200, json={"choices": [{"message": {"content": "done"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = AsyncLLMClient(client)
        if status == 401:
            with pytest.raises(httpx.HTTPStatusError):
                await llm.generate("test")
        else:
            assert await llm.generate("test") == "done"
    assert len(calls) == expected_calls
