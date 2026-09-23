import pytest

from src.config.settings import settings
from src.utils.llm_client import LLMClient

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining,expected", [(125, 125), (600, 180), (None, 180)])
async def test_async_chat_preserves_run_timeout_and_finish_reason(monkeypatch, remaining, expected):
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 1024}}

    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            captured["payload"] = kwargs["json"]
            return Response()

    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_reasoning_effort", "low")
    monkeypatch.setattr(settings, "llm_timeout_seconds", 180)
    monkeypatch.setattr("src.utils.llm_client.httpx.AsyncClient", Client)
    response = await LLMClient().acomplete([{"role": "user", "content": "q"}], timeout=remaining)
    assert captured["timeout"] == expected
    assert captured["payload"]["reasoning_effort"] == "low"
    assert captured["payload"]["max_tokens"] == 1024
    assert response["finish_reason"] == "length"
    assert response["usage"]["completion_tokens"] == 1024
