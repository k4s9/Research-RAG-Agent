"""Document outline and contiguous range selection.

The outline prefers the layout heuristics stored at ingest time and falls back
to the chunk locators, so the contract stays stable when Phase F replaces the
PDF layout implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_RANGE_CHARS = 6000
MAX_RANGE_CHARS = 20000


class ChunkLike(Protocol):
    """Duck-typed chunk: the columns ``document_view`` actually reads."""

    id: str
    chunk_index: int
    content: str
    content_type: str
    chunk_metadata: dict[str, Any] | None


@dataclass(frozen=True)
class ChunkExcerpt:
    """A bounded view; clipping must never mutate the authoritative ORM row."""

    id: str
    chunk_index: int
    content: str
    content_type: str
    chunk_metadata: dict[str, Any] | None
    char_start: int
    char_end: int


class DocumentLike(Protocol):
    """Duck-typed document: only the stored parse metadata is used."""

    parse_metadata: dict[str, Any] | None


def _chunk_locator(chunk: ChunkLike) -> dict[str, Any]:
    metadata = chunk.chunk_metadata or {}
    if metadata.get("section_path"):
        return {
            "file_type": "markdown",
            "section_path": list(metadata["section_path"]),
            "line_start": metadata.get("section_start_line"),
            "line_end": metadata.get("section_end_line"),
        }
    return {
        "file_type": "pdf",
        "page_start": metadata.get("page_start", metadata.get("page_num")),
        "page_end": metadata.get("page_end", metadata.get("page_num")),
    }


def outline_from_chunks(chunks: list[ChunkLike]) -> list[dict[str, Any]]:
    """Derive an outline from chunk locators when no stored outline exists."""
    entries: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, ...]] = set()
    seen_pages: set[int] = set()
    for chunk in chunks:
        metadata = chunk.chunk_metadata or {}
        section_path = metadata.get("section_path") or []
        if section_path:
            key = tuple(str(part) for part in section_path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            entries.append(
                {
                    "title": key[-1],
                    "level": int(metadata.get("heading_level") or len(key)),
                    "section_path": list(key),
                    "line_start": metadata.get("section_start_line"),
                    "line_end": metadata.get("section_end_line"),
                },
            )
            continue
        page = metadata.get("page_start", metadata.get("page_num"))
        if isinstance(page, int) and page not in seen_pages:
            seen_pages.add(page)
            entries.append({"title": f"第 {page} 页", "level": 1, "page": page})
    return entries


def build_document_outline(document: DocumentLike, chunks: list[ChunkLike]) -> list[dict[str, Any]]:
    """Return the stored outline, falling back to chunk-derived entries."""
    stored = (document.parse_metadata or {}).get("outline")
    if isinstance(stored, list) and stored:
        return [dict(entry) for entry in stored if isinstance(entry, dict)]
    return outline_from_chunks(chunks)


def chunk_page_range(chunk: ChunkLike) -> tuple[int | None, int | None]:
    metadata = chunk.chunk_metadata or {}
    start = metadata.get("page_start", metadata.get("page_num"))
    end = metadata.get("page_end", start)
    return (start if isinstance(start, int) else None, end if isinstance(end, int) else None)


def _matches_page_range(chunk: ChunkLike, page_start: int | None, page_end: int | None) -> bool:
    first, last = chunk_page_range(chunk)
    if page_start is not None and (last is None or last < page_start):
        return False
    if page_end is not None and (first is None or first > page_end):
        return False
    return True


def _matches_section_path(chunk: ChunkLike, section_path: list[str]) -> bool:
    metadata = chunk.chunk_metadata or {}
    chunk_path = [str(part) for part in metadata.get("section_path") or []]
    if not chunk_path:
        return False
    return chunk_path[: len(section_path)] == [str(part) for part in section_path]


def select_document_range(
    chunks: list[ChunkLike],
    *,
    page_start: int | None = None,
    page_end: int | None = None,
    section_path: list[str] | None = None,
    max_chars: int = DEFAULT_RANGE_CHARS,
    start_chunk_index: int = 0,
    start_char: int = 0,
) -> tuple[list[ChunkExcerpt], dict[str, int] | None]:
    """Select document-ordered, contiguous chunks for a page or section range.

    ``max_chars`` bounds the joined text, including separators. The second
    return value is the exact next chunk/character offset, or None at the end.
    """
    limit = max(1, min(int(max_chars), MAX_RANGE_CHARS))
    ordered = sorted(chunks, key=lambda chunk: chunk.chunk_index)
    selected: list[ChunkExcerpt] = []
    used = 0
    for chunk in ordered:
        if chunk.chunk_index < start_chunk_index:
            continue
        if page_start is not None or page_end is not None:
            if not _matches_page_range(chunk, page_start, page_end):
                continue
        if section_path and not _matches_section_path(chunk, section_path):
            continue
        offset = start_char if chunk.chunk_index == start_chunk_index else 0
        if offset >= len(chunk.content):
            continue
        separator_chars = 2 if selected else 0
        remaining = limit - used - separator_chars
        if remaining <= 0:
            return selected, {"start_chunk_index": chunk.chunk_index, "start_char": offset}
        end = min(len(chunk.content), offset + remaining)
        selected.append(
            ChunkExcerpt(
                id=chunk.id,
                chunk_index=chunk.chunk_index,
                content=chunk.content[offset:end],
                content_type=chunk.content_type,
                chunk_metadata=chunk.chunk_metadata,
                char_start=offset,
                char_end=end,
            ),
        )
        used += end - offset + separator_chars
        if end < len(chunk.content):
            return selected, {"start_chunk_index": chunk.chunk_index, "start_char": end}
    return selected, None
