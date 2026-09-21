import pytest

from src.core.ingest.metadata import extract_document_metadata

pytestmark = pytest.mark.unit


def span(text: str, y: float, size: float, x: float = 72.0) -> dict:
    return {"text": text, "bbox": (x, y, x + 6 * len(text), y + size), "font_size": size}


def pdf_page(page_num: int, text: str, blocks: list[dict]) -> dict:
    return {"page_num": page_num, "text": text, "blocks": blocks}


def test_pdf_metadata_uses_font_size_and_position_heuristics() -> None:
    parsed = {
        "file_type": "pdf",
        "pages": [
            pdf_page(
                1,
                "Attention Is All You Need\nAshish Vaswani, Noam Shazeer\nAbstract\n"
                "arXiv:1706.03762 2017",
                [
                    span("Attention Is All You Need", 60, 18),
                    span("Ashish Vaswani, Noam Shazeer", 90, 10),
                    span("Abstract", 130, 10),
                    span("The dominant sequence transduction models are recurrent.", 150, 10),
                    span("1 Introduction", 300, 14),
                ],
            ),
            pdf_page(2, "more text", [span("2 Background", 60, 14), span("body", 80, 10)]),
        ],
    }

    metadata = extract_document_metadata(parsed, "1706.03762.pdf")

    assert metadata["title"] == "Attention Is All You Need"
    assert metadata["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert metadata["year"] == 2017
    assert metadata["venue"] == "arXiv"
    assert metadata["doc_type"] == "paper"
    assert metadata["page_count"] == 2
    assert metadata["outline"] == [
        {"title": "Attention Is All You Need", "page": 1, "level": 1},
        {"title": "1 Introduction", "page": 1, "level": 2},
        {"title": "2 Background", "page": 2, "level": 2},
    ]


def test_pdf_without_paper_markers_falls_back_to_filename_and_report_type() -> None:
    parsed = {
        "file_type": "pdf",
        "pages": [
            pdf_page(1, "Weekly progress", [span("Weekly progress", 60, 11)]),
        ],
    }

    metadata = extract_document_metadata(parsed, "weekly-2026.pdf")

    assert metadata["title"] == "Weekly progress"
    assert metadata["authors"] == []
    assert metadata["year"] is None
    assert metadata["venue"] is None
    assert metadata["doc_type"] == "report"
    assert metadata["page_count"] == 1


def test_markdown_metadata_uses_heading_and_author_lines() -> None:
    parsed = {
        "file_type": "markdown",
        "sections": [
            {
                "level": 0,
                "heading": "Document Root",
                "content": "作者: Zhang San, Li Si\n2024 年 3 月",
                "start_line": 1,
                "end_line": 3,
                "section_path": [],
            },
            {
                "level": 1,
                "heading": "实验方案",
                "content": "正文",
                "start_line": 4,
                "end_line": 9,
                "section_path": ["实验方案"],
            },
        ],
    }

    metadata = extract_document_metadata(parsed, "plan.md")

    assert metadata["title"] == "实验方案"
    assert metadata["authors"] == ["Zhang San", "Li Si"]
    assert metadata["year"] == 2024
    assert metadata["doc_type"] == "note"
    assert metadata["page_count"] is None
    assert metadata["outline"] == [
        {
            "title": "实验方案",
            "level": 1,
            "section_path": ["实验方案"],
            "line_start": 4,
            "line_end": 9,
        },
    ]


def test_markdown_report_marker_and_missing_headings_are_handled() -> None:
    parsed = {
        "file_type": "markdown",
        "sections": [
            {
                "level": 0,
                "heading": "Document Root",
                "content": "季度汇报材料",
                "start_line": 1,
                "end_line": 2,
                "section_path": [],
            },
        ],
    }

    metadata = extract_document_metadata(parsed, "q3-汇报.md")

    assert metadata["doc_type"] == "report"
    assert metadata["title"] == "q3-汇报"
    assert metadata["outline"] == []
