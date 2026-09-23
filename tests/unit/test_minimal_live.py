"""Offline checks of live experiment accounting; these do not exercise a real model."""

import json

import httpx
import pytest

from scripts.run_minimal_live import record_http


@pytest.mark.asyncio
async def test_real_transport_recorder_preserves_raw_usage_and_limits_probe(tmp_path):
    payload = dict(model="Qwen3.8-27B", reasoning_effort="low", temperature=0,
                   messages=[{"role": "user", "content": "test"}], max_tokens=2048)
    raw = {"choices": [{"finish_reason": "stop", "message": {"content": "ALPHA"}}],
           "usage": {"prompt_tokens": 20, "completion_tokens": 5}}
    calls = []

    async def respond(request):
        calls.append(request)
        return httpx.Response(200, json=raw)

    with record_http(tmp_path, ["probe"]) as ledger:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await client.post("http://test/v1/chat/completions", json=payload)
            with pytest.raises(RuntimeError, match="probe budget"):
                await client.post("http://test/v1/chat/completions", json=payload)
        assert len(calls) == 1
        assert ledger["accounted_tokens"] == 25
        assert ledger["unknown_usage_calls"] == 0
    entry = json.loads((tmp_path / "http-generation-01.json").read_text())
    assert json.loads(entry["raw_response"]) == raw
    assert entry["finish_reasons"] == ["stop"]


@pytest.mark.asyncio
async def test_timeout_keeps_unknown_reservation_and_original_failure(tmp_path):
    async def timeout(request):
        raise httpx.ReadTimeout("offline simulated timeout", request=request)

    with record_http(tmp_path, ["probe"]) as ledger:
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            with pytest.raises(httpx.ReadTimeout):
                await client.post("http://test/v1/chat/completions", json=dict(
                    model="Qwen3.8-27B", reasoning_effort="low", temperature=0,
                    messages=[{"role": "user", "content": "test"}], max_tokens=2048))
        assert ledger["generation"] == ledger["unknown_usage_calls"] == 1
        assert ledger["accounted_tokens"] > 2048
        assert ledger["cost"] is None
    entry = json.loads((tmp_path / "http-generation-01.json").read_text())
    assert entry["status"] == "failed"
    assert entry["error_type"] == "ReadTimeout"
