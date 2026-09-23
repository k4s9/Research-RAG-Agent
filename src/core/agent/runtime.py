"""Serializable run-local evidence and resource accounting."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field

from src.config.settings import settings


class RunBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_calls: int = Field(default=6, ge=1, le=24)
    tool_calls: int = Field(default=8, ge=1, le=48)
    active_seconds: float = Field(default=600, gt=0, le=900)
    total_tokens: int = Field(default=128000, ge=1, le=1000000)
    context_tokens: int = Field(default=65536, ge=1, le=262144)
    output_tokens: int = Field(default=2048, ge=1, le=8192)
    result_chars: int = Field(default=30000, ge=1, le=100000)
    total_result_chars: int = Field(default=120000, ge=1, le=1000000)

    @classmethod
    def configured(cls):
        return cls(
            model_calls=settings.agent_max_model_calls,
            tool_calls=settings.agent_max_tool_calls,
            active_seconds=settings.agent_max_active_seconds,
            total_tokens=settings.agent_max_total_tokens,
            context_tokens=settings.agent_max_context_tokens,
            output_tokens=settings.agent_max_output_tokens,
            result_chars=settings.agent_max_result_chars,
            total_result_chars=settings.agent_max_total_result_chars,
        )


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def token_upper_bound(value) -> int:
    """UTF-8 byte count + framing reserve; conservative, not a measured tokenizer."""
    return len(encoded(value).encode("utf-8")) + 128


def model_observation(result: dict) -> dict:
    """Keep evidence text/locators in context; retain retrieval diagnostics in trace only."""
    result = deepcopy(result)
    evidence_fields = {
        "id", "chunk_id", "document_id", "source_id", "content", "filename", "locator",
        "char_start", "char_end", "content_type", "entity_type", "version_status",
    }
    for key in ("results", "chunks"):
        if isinstance(result.get(key), list):
            result[key] = [
                {k: v for k, v in item.items() if k in evidence_fields}
                for item in result[key]
            ]
    return result


class EvidenceRegistry:
    def __init__(self, sources=None):
        self.sources = deepcopy(sources or {})

    def register(self, item: dict, parent: dict | None = None) -> dict:
        parent = parent or {}
        content = str(item.get("content", ""))
        chunk_id = item.get("chunk_id") or item.get("id")
        if not chunk_id or not content:
            return dict(item)
        source = {
            "chunk_id": chunk_id,
            "document_id": item.get("document_id") or parent.get("document_id"),
            "document_hash": item.get("document_hash") or parent.get("document_hash"),
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "filename": item.get("filename") or parent.get("filename", ""),
            "locator": deepcopy(item.get("locator", {})),
            "char_start": item.get("char_start", 0),
            "char_end": item.get("char_end", len(content)),
            "content": content,
            "quote": content[:500],
        }
        identity = (
            "chunk_id",
            "document_id",
            "document_hash",
            "content_hash",
            "char_start",
            "char_end",
        )
        source_id = next(
            (
                sid
                for sid, old in self.sources.items()
                if all(old.get(k) == source.get(k) for k in identity)
            ),
            None,
        )
        if source_id is None:
            source_id = f"S{max([int(k[1:]) for k in self.sources] or [0]) + 1}"
            self.sources[source_id] = {"source_id": source_id, **source}
        return {**item, "source_id": source_id}

    def register_result(self, result: dict) -> dict:
        result = deepcopy(result)
        for key in ("results", "chunks"):
            if isinstance(result.get(key), list):
                result[key] = [self.register(item, result) for item in result[key]]
        if result.get("chunk_id"):
            result = self.register(result)
        return result


def empty_usage() -> dict:
    return dict(
        model_calls=0,
        tool_calls=0,
        prompt_tokens=0,
        completion_tokens=0,
        reserved_tokens=0,
        unknown_usage_calls=0,
        result_chars=0,
        active_seconds=0.0,
        model_seconds=0.0,
        tool_seconds=0.0,
        cost=None,
        cost_currency="unknown",
        token_estimation="utf8_upper_bound",
    )


def price_usage(usage: dict) -> dict:
    usage = dict(usage)
    if (
        not usage["unknown_usage_calls"]
        and settings.llm_input_price_per_million is not None
        and settings.llm_output_price_per_million is not None
    ):
        usage["cost"] = (
            usage["prompt_tokens"] * settings.llm_input_price_per_million
            + usage["completion_tokens"] * settings.llm_output_price_per_million
        ) / 1e6
        usage["cost_currency"] = "configured_currency"
    usage["usage_status"] = "unknown" if usage["unknown_usage_calls"] else "measured"
    return usage
