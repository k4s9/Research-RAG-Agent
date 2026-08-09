import pytest

from src.core.agent.orchestrator import AgentOrchestrator

pytestmark = pytest.mark.unit


class FakeSearcher:
    def search(self, **kwargs):
        return [{"id": "chunk-1", "score": 0.9}]


class FakeLLM:
    def generate(self, **kwargs):
        assert "[S1]" in kwargs["prompt"]
        return "Supported claim [S1]. Invalid claim [S99]."


async def fake_hydrator(results, project_ids):
    assert project_ids == ["project-1"]
    return [
        {
            **results[0],
            "content": "Supporting evidence",
            "filename": "notes.md",
            "source": "notes.md",
            "locator": {"file_type": "markdown", "section_path": ["Evidence"]},
        }
    ]


@pytest.mark.asyncio
async def test_orchestrator_returns_only_valid_structured_citations() -> None:
    orchestrator = AgentOrchestrator(
        llm_client=FakeLLM(), searcher=FakeSearcher(), hydrator=fake_hydrator
    )

    result = await orchestrator.handle_message("Question", ["project-1"], "session-1")

    assert [citation["source_id"] for citation in result["citations"]] == ["S1"]
    assert result["citations"][0]["chunk_id"] == "chunk-1"
    assert result["invalid_citation_ids"] == ["S99"]
    assert result["retrieved_context"][0]["content_preview"] == "Supporting evidence"
