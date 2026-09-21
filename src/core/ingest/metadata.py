"""Heuristic document metadata and outline extraction.

No LLM is involved here: title/authors/year/doc_type come from cheap layout
signals (font size, position, first lines) and are correctable through
``PATCH /documents/{id}``. Summaries and tags are produced asynchronously by
the LLM enricher instead.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DOC_TYPES = ("paper", "report", "note", "other")
MAX_TITLE_CHARS = 200
MAX_OUTLINE_ENTRIES = 200
OUTLINE_TITLE_CHARS = 120
TITLE_FONT_RATIO = 1.15
MAX_OUTLINE_LEVELS = 3
AUTHOR_WINDOW_POINTS = 150
MAX_AUTHOR_LINE_CHARS = 200

YEAR_PATTERN = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")
ARXIV_PATTERN = re.compile(r"arxiv[:\s/]*(\d{4}\.\d{4,5})", re.IGNORECASE)
PAPER_MARKERS = ("abstract", "references", "doi", "arxiv")
REPORT_MARKERS = ("报告", "汇报", "slides", "presentation", "report", "周报", "月报")
AUTHOR_LINE_PATTERN = re.compile(r"^\s*(?:作者|authors?|author)\s*[:：]\s*(.+)$", re.IGNORECASE)
AUTHOR_SPLIT_PATTERN = re.compile(r"[,，、;；]|\band\b")


def _stem(filename: str) -> str:
    return Path(filename).stem.strip() or filename


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _page_lines(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Group spans into visual lines ordered by vertical position."""
    grouped: dict[float, list[dict[str, Any]]] = {}
    for span in page.get("blocks") or []:
        text = str(span.get("text") or "").strip()
        if not text:
            continue
        bbox = span.get("bbox") or (0.0, 0.0, 0.0, 0.0)
        key = round(float(bbox[1]), 1)
        grouped.setdefault(key, []).append(span)
    lines: list[dict[str, Any]] = []
    for key in sorted(grouped):
        spans = sorted(grouped[key], key=lambda span: float((span.get("bbox") or (0,))[0]))
        text = re.sub(r"\s+", " ", " ".join(str(span.get("text") or "") for span in spans)).strip()
        if not text:
            continue
        sizes = [float(span.get("font_size") or 0.0) for span in spans]
        lines.append({"text": text, "font_size": max(sizes), "y": key})
    return lines


def _first_year(text: str) -> int | None:
    match = YEAR_PATTERN.search(text)
    return int(match.group(1)) if match else None


def _looks_like_person_name(token: str) -> bool:
    stripped = token.strip()
    if not stripped or len(stripped) > 60:
        return False
    if any(character.isdigit() for character in stripped):
        return False
    words = stripped.replace(".", " ").split()
    if not words or len(words) > 5:
        return False
    return all(word[:1].isupper() or not word[:1].isascii() for word in words)


def _split_authors(line: str) -> list[str]:
    authors = []
    for token in AUTHOR_SPLIT_PATTERN.split(line):
        candidate = " ".join(token.split()).strip(" ,，、;；")
        if candidate and _looks_like_person_name(candidate):
            authors.append(candidate)
    return authors


def _pdf_title(pages: list[dict[str, Any]], filename: str) -> str:
    if not pages:
        return _stem(filename)
    lines = _page_lines(pages[0])
    if not lines:
        return _stem(filename)
    sizes = [line["font_size"] for line in lines if line["font_size"] > 0]
    baseline = _median(sizes)
    candidates = [
        line
        for line in lines
        if line["font_size"] >= baseline * TITLE_FONT_RATIO
        and 0 < len(line["text"]) <= MAX_TITLE_CHARS
        and not line["text"].isdigit()
    ]
    if candidates:
        best = max(candidates, key=lambda line: (line["font_size"], -line["y"]))
        return best["text"]
    first_line = lines[0]["text"]
    if 0 < len(first_line) <= TITLE_FONT_RATIO * 120 and not first_line.isdigit():
        return first_line
    return _stem(filename)


def _pdf_authors(pages: list[dict[str, Any]], title: str) -> list[str]:
    if not pages:
        return []
    lines = _page_lines(pages[0])
    title_line = next(
        (line for line in lines if line["text"] == title),
        lines[0] if lines else None,
    )
    if title_line is None:
        return []
    for line in lines:
        if line["y"] <= title_line["y"]:
            continue
        if line["y"] - title_line["y"] > AUTHOR_WINDOW_POINTS:
            break
        if len(line["text"]) > MAX_AUTHOR_LINE_CHARS:
            continue
        authors = _split_authors(line["text"])
        if authors:
            return authors
    return []


def _body_font_size(sizes: list[float]) -> float:
    """Most common (mode) line size in the document, i.e. the body text size."""
    if not sizes:
        return 0.0
    counts: dict[float, int] = {}
    for size in sizes:
        counts[size] = counts.get(size, 0) + 1
    return min(counts, key=lambda size: (-counts[size], size))


def _pdf_outline(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build an outline from font size + vertical position.

    The body size is derived from the whole document (not per page) so the same
    section size gets the same level on every page. Remaining sizes above the
    heading threshold are ranked by size: the largest is level 1, the next level
    2, and so on.
    """
    pages_lines = [_page_lines(page) for page in pages]
    all_sizes = [
        line["font_size"] for lines in pages_lines for line in lines if line["font_size"] > 0
    ]
    baseline = _body_font_size(all_sizes)
    if baseline <= 0:
        return []
    heading_sizes = sorted(
        {size for size in all_sizes if size >= baseline * TITLE_FONT_RATIO},
        reverse=True,
    )
    levels = {size: min(rank + 1, MAX_OUTLINE_LEVELS) for rank, size in enumerate(heading_sizes)}

    entries: list[dict[str, Any]] = []
    for page, lines in zip(pages, pages_lines, strict=True):
        for line in lines:
            text = line["text"]
            if len(text) < 2 or len(text) > OUTLINE_TITLE_CHARS or text.isdigit():
                continue
            level = levels.get(line["font_size"])
            if level is None:
                continue
            if entries and entries[-1]["title"] == text:
                continue
            entries.append({"title": text, "page": page.get("page_num", 1), "level": level})
            if len(entries) >= MAX_OUTLINE_ENTRIES:
                return entries
    return entries


def _markdown_outline(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = []
    for section in sections:
        heading = (section.get("heading") or "").strip()
        level = section.get("level") or 0
        if not heading or level <= 0:
            continue
        entries.append(
            {
                "title": heading,
                "level": level,
                "section_path": section.get("section_path") or [heading],
                "line_start": section.get("start_line"),
                "line_end": section.get("end_line"),
            },
        )
        if len(entries) >= MAX_OUTLINE_ENTRIES:
            break
    return entries


def _extract_pdf_metadata(parsed_content: dict[str, Any], filename: str) -> dict[str, Any]:
    pages = parsed_content.get("pages") or []
    head_text = "\n".join(str(page.get("text") or "") for page in pages[:2])
    title = _pdf_title(pages, filename)
    venue = "arXiv" if ARXIV_PATTERN.search(head_text) else None
    return {
        "title": title,
        "authors": _pdf_authors(pages, title),
        "year": _first_year(head_text),
        "venue": venue,
        "doc_type": (
            "paper" if any(marker in head_text.lower() for marker in PAPER_MARKERS) else "report"
        ),
        "page_count": len(pages),
        "outline": _pdf_outline(pages),
        "extra_metadata": {"source": "pdf_heuristic"},
    }


def _extract_markdown_metadata(parsed_content: dict[str, Any], filename: str) -> dict[str, Any]:
    sections = parsed_content.get("sections") or []
    head_lines = []
    for section in sections[:3]:
        head_lines.extend(str(section.get("content") or "").splitlines())
    head_text = "\n".join(head_lines[:40])
    title = ""
    for section in sections:
        heading = (section.get("heading") or "").strip()
        if heading and (section.get("level") or 0) == 1:
            title = heading
            break
    if not title:
        title = _stem(filename)
    authors: list[str] = []
    for line in head_text.splitlines():
        match = AUTHOR_LINE_PATTERN.match(line)
        if match:
            authors = _split_authors(match.group(1))
            break
    haystack = f"{title} {filename} {head_text[:500]}".lower()
    doc_type = "report" if any(marker in haystack for marker in REPORT_MARKERS) else "note"
    return {
        "title": title,
        "authors": authors,
        "year": _first_year(head_text),
        "venue": None,
        "doc_type": doc_type,
        "page_count": None,
        "outline": _markdown_outline(sections),
        "extra_metadata": {"source": "markdown_heuristic"},
    }


def extract_document_metadata(parsed_content: dict[str, Any], filename: str) -> dict[str, Any]:
    """Return title/authors/year/venue/doc_type/page_count/outline for a parsed document."""
    if parsed_content.get("file_type") == "pdf":
        return _extract_pdf_metadata(parsed_content, filename)
    return _extract_markdown_metadata(parsed_content, filename)
