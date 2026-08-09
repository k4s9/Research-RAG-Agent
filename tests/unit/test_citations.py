import pytest

from src.core.agent.prompt_templates import get_rag_prompt
from src.core.citations import (
    extract_citation_ids,
    source_locator,
    validate_citation_ids,
)

pytestmark = pytest.mark.unit


def test_source_locator_preserves_pdf_and_markdown_coordinates() -> None:
    assert source_locator("paper.pdf", "pdf", {"page_start": 2, "page_end": 3}) == {
        "file_type": "pdf",
        "page_start": 2,
        "page_end": 3,
    }
    assert source_locator(
        "notes.md",
        "markdown",
        {"section_path": ["Methods", "Retrieval"], "section_start_line": 8, "section_end_line": 12},
    )["section_path"] == ["Methods", "Retrieval"]


def test_citation_parser_deduplicates_and_rejects_unknown_sources() -> None:
    answer = "Claim [S2]. Another claim [S1][S2]. Invalid [S99]."
    assert extract_citation_ids(answer) == ["S2", "S1", "S99"]
    assert validate_citation_ids(answer, {"S1": {}, "S2": {}}) == ["S2", "S1"]


def test_rag_prompt_exposes_only_numbered_sources_and_abstention_rule() -> None:
    prompt = get_rag_prompt(
        "What happened?",
        [{"content": "Evidence", "filename": "notes.md", "locator": {"section_path": ["A"]}}],
    )
    assert "[S1]" in prompt
    assert "不得编造来源标识" in prompt
