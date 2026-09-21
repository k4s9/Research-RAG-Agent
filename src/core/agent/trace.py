"""Summarize tool results and address their full payloads.

Tool payloads never go back into the model context in full: ``agent_step``
keeps a bounded ``result_summary`` for replay and a ``result_ref`` that points
at the authoritative payload (chunks, documents, memories).
"""

from __future__ import annotations

import json

MAX_RESULT_SUMMARY_CHARS = 800
MAX_RESULT_REF_CHARS = 512
MAX_REFERENCED_CHUNKS = 20
PREVIEW_CHARS = 160
MAX_SUMMARIZED_ITEMS = 5


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + "…"


def _preview(value: object) -> str:
    text = " ".join(str(value or "").split())
    return _truncate(text, PREVIEW_CHARS)


def _describe_locator(locator: object) -> str:
    if not isinstance(locator, dict):
        return ""
    if locator.get("file_type") == "pdf":
        page_start = locator.get("page_start")
        page_end = locator.get("page_end")
        if page_start is not None and page_end is not None and page_end != page_start:
            return f"p.{page_start}-{page_end}"
        if page_start is not None:
            return f"p.{page_start}"
        return ""
    section_path = locator.get("section_path") or []
    if section_path:
        return " > ".join(str(part) for part in section_path)
    return ""


def summarize_tool_result(result: object, error: str | None = None) -> str:
    """Build the bounded (≤ 800 chars) text that is fed back to the model."""
    if error:
        return _truncate(f"工具执行失败: {error}", MAX_RESULT_SUMMARY_CHARS)
    if not isinstance(result, dict):
        return _truncate(str(result), MAX_RESULT_SUMMARY_CHARS)
    if isinstance(result.get("documents"), list):
        items = [item for item in result["documents"] if isinstance(item, dict)]
        parts = [f"列出 {len(items)} 篇材料"]
        for index, item in enumerate(items[:MAX_SUMMARIZED_ITEMS], start=1):
            parts.append(
                " ".join(
                    [
                        f"{index})",
                        str(item.get("title") or item.get("filename") or "unknown"),
                        f"[{item.get('doc_type') or 'other'}]",
                        f"({item.get('year')})" if item.get("year") else "",
                        _preview(item.get("summary") or item.get("filename")),
                    ],
                ).replace("  ", " "),
            )
        if len(items) > MAX_SUMMARIZED_ITEMS:
            parts.append(f"…另有 {len(items) - MAX_SUMMARIZED_ITEMS} 篇见 result_ref")
        return _truncate("；".join(parts), MAX_RESULT_SUMMARY_CHARS)
    if isinstance(result.get("chunks"), list):
        items = [item for item in result["chunks"] if isinstance(item, dict)]
        indexes = [item.get("chunk_index") for item in items if item.get("chunk_index") is not None]
        span = f"chunk {min(indexes)}-{max(indexes)}" if indexes else "0 段"
        preview = _preview(items[0].get("content")) if items else ""
        truncated = "，已截断" if result.get("truncated") else ""
        return _truncate(
            f"按文档顺序连续读取 {len(items)} 段（{span}{truncated}）：{preview}",
            MAX_RESULT_SUMMARY_CHARS,
        )
    if isinstance(result.get("outline"), list):
        items = [item for item in result["outline"] if isinstance(item, dict)]
        titles = "；".join(
            f"{item.get('title')}" + (f"(p.{item['page']})" if item.get("page") else "")
            for item in items[:MAX_SUMMARIZED_ITEMS]
        )
        return _truncate(
            f"文档 outline 共 {len(items)} 个标题：{titles}",
            MAX_RESULT_SUMMARY_CHARS,
        )
    if isinstance(result.get("results"), list):
        items = [item for item in result["results"] if isinstance(item, dict)]
        parts = [f"检索命中 {len(items)} 段"]
        for index, item in enumerate(items[:MAX_SUMMARIZED_ITEMS], start=1):
            descriptor = [
                f"{index})",
                str(item.get("filename") or item.get("source") or "unknown"),
                _describe_locator(item.get("locator")),
                _preview(item.get("content") or item.get("content_preview")),
            ]
            parts.append(" ".join(part for part in descriptor if part))
        if len(items) > MAX_SUMMARIZED_ITEMS:
            parts.append(f"…另有 {len(items) - MAX_SUMMARIZED_ITEMS} 段见 result_ref")
        return _truncate("；".join(parts), MAX_RESULT_SUMMARY_CHARS)
    if result.get("chunk_id"):
        source = result.get("filename") or _describe_locator(result.get("locator"))
        header = f"读取 chunk {result['chunk_id']}" + (f"（{source}）" if source else "")
        return _truncate(f"{header}: {_preview(result.get('content'))}", MAX_RESULT_SUMMARY_CHARS)
    if result.get("memory_id"):
        summary = _preview(result.get("summary"))
        return _truncate(
            f"保存记忆 {result['memory_id']}: {summary}",
            MAX_RESULT_SUMMARY_CHARS,
        )
    return _truncate(json.dumps(result, ensure_ascii=False, default=str), MAX_RESULT_SUMMARY_CHARS)


def build_result_ref(tool_name: str | None, result: object) -> str | None:
    """Address the full tool payload so the summary stays small."""
    if not isinstance(result, dict):
        return None
    if isinstance(result.get("documents"), list):
        document_ids = [
            str(item["id"])
            for item in result["documents"][:MAX_REFERENCED_CHUNKS]
            if isinstance(item, dict) and item.get("id")
        ]
        if document_ids:
            return _truncate("documents:" + ",".join(document_ids), MAX_RESULT_REF_CHARS)
    if isinstance(result.get("outline"), list) and result.get("document_id"):
        return _truncate(f"document:{result['document_id']}:outline", MAX_RESULT_REF_CHARS)
    if isinstance(result.get("chunks"), list):
        chunk_ids = [
            str(item["chunk_id"])
            for item in result["chunks"][:MAX_REFERENCED_CHUNKS]
            if isinstance(item, dict) and item.get("chunk_id")
        ]
        if chunk_ids:
            return _truncate("chunks:" + ",".join(chunk_ids), MAX_RESULT_REF_CHARS)
    if isinstance(result.get("results"), list):
        chunk_ids = [
            str(item["id"])
            for item in result["results"][:MAX_REFERENCED_CHUNKS]
            if isinstance(item, dict) and item.get("id")
        ]
        if chunk_ids:
            return _truncate("chunks:" + ",".join(chunk_ids), MAX_RESULT_REF_CHARS)
    if result.get("chunk_id"):
        return _truncate(f"chunk:{result['chunk_id']}", MAX_RESULT_REF_CHARS)
    if result.get("document_id"):
        return _truncate(f"document:{result['document_id']}", MAX_RESULT_REF_CHARS)
    if result.get("memory_id"):
        return _truncate(f"memory:{result['memory_id']}", MAX_RESULT_REF_CHARS)
    return None
