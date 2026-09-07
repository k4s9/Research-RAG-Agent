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
