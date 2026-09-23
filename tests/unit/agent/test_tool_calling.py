import json

import pytest

from src.core.agent.orchestrator import AgentOrchestrator
from src.core.agent.tools import ResearchToolRegistry, ToolExecutionError


pytestmark = pytest.mark.unit


class FakeToolLLM:
    supports_tool_calling = True

    def __init__(self) -> None:
        self.calls = 0

    def generate_with_tools(self, prompt: str, **kwargs):
        del prompt, kwargs
        self.calls += 1
        if self.calls == 1:
            return {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "search_knowledge",
                                "arguments": json.dumps({"query": "calibration"}),
                            },
                        }
                    ],
                }
            }
        return {"message": {"role": "assistant", "content": "Evidence [S1]"}}


class FakeRegistry:
    async def execute(self, name, arguments, **kwargs):
        assert name == "search_knowledge"
        assert arguments == {"query": "calibration"}
        return {
            "results": [
                {
                    "id": "chunk-1",
                    "content": "Calibration is verified.",
                    "filename": "paper.pdf",
                    "locator": {"file_type": "pdf", "page_start": 2},
                    "score": 0.9,
                }
            ]
        }


@pytest.mark.asyncio
async def test_native_tool_loop_returns_structured_citation() -> None:
    orchestrator = AgentOrchestrator(
        llm_client=FakeToolLLM(),
        searcher=object(),
        tool_registry=FakeRegistry(),
    )

    result = await orchestrator.handle_message("Where?", ["project-1"], "session-1")

    assert result["response"] == "Evidence [S1]"
    assert result["citations"][0]["chunk_id"] == "chunk-1"
    assert result["citations"][0]["locator"]["page_start"] == 2
    assert result["tool_trace"][0]["tool"] == "search_knowledge"


def test_tool_scope_cannot_expand_caller_projects() -> None:
    with pytest.raises(ToolExecutionError, match="exceeds"):
        ResearchToolRegistry._scope(["other-project"], ["project-1"])


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", [None, 1, 20])
async def test_agent_search_top_k_is_enforced_and_observable(monkeypatch, requested):
    from src.config.settings import settings

    monkeypatch.setattr(settings, "agent_search_top_k", 3)
    captured = {}

    class Searcher:
        def search(self, **kwargs):
            captured.update(kwargs)
            return [{"id": str(i), "content": "evidence"} for i in range(6)]

    async def hydrate(results, project_ids):
        return results

    args = {"query": "memory"}
    if requested is not None:
        args["top_k"] = requested
    result = await ResearchToolRegistry(Searcher(), hydrator=hydrate).search_knowledge(
        args, project_ids=["p"], session_id="s"
    )
    expected = min(requested or 3, 3)
    assert captured["top_k"] == expected
    assert len(result["results"]) == expected
    assert result["requested_top_k"] == (requested or 3)
    assert result["effective_top_k"] == expected
