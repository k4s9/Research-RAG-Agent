from pathlib import Path

import fitz
import pytest

from src.core.ingest.markdown_chunker import MarkdownChunker
from src.core.ingest.markdown_parser import EnhancedMarkdownParser
from src.core.ingest.pdf_chunker import PDFChunker
from src.core.ingest.pdf_parser import PDFParser


pytestmark = pytest.mark.unit


def test_pdf_chunker_preserves_one_based_page_locator() -> None:
    parsed = {
        "file_type": "pdf",
        "pages": [
            {"page_num": 1, "text": "Evidence on page one. " * 20},
            {"page_num": 2, "text": "Evidence on page two. " * 20},
        ],
    }
    chunks = PDFChunker(chunk_size=12, overlap=3).chunk(parsed)

    assert chunks
    for chunk in chunks:
        assert chunk.metadata["page_num"] in {1, 2}
        assert chunk.metadata["page_start"] == chunk.metadata["page_num"]
        assert chunk.metadata["page_end"] == chunk.metadata["page_num"]


def test_markdown_chunker_preserves_section_path_and_lines() -> None:
    parsed = {
        "file_type": "markdown",
        "sections": [
            {
                "heading": "Retrieval",
                "level": 2,
                "content": "Dense retrieval evidence.",
                "start_line": 4,
                "end_line": 5,
                "section_path": ["Research Notes", "Retrieval"],
            }
        ],
    }
    chunks = MarkdownChunker(chunk_size=50, min_chunk_size=1).chunk(parsed)

    assert chunks
    metadata = chunks[0].metadata
    assert metadata["section_path"] == ["Research Notes", "Retrieval"]
    assert metadata["section_start_line"] == 4
    assert metadata["section_end_line"] == 5


@pytest.mark.asyncio
async def test_markdown_parser_preserves_nested_section_path(tmp_path: Path) -> None:
    markdown_path = tmp_path / "notes.md"
    markdown_path.write_text(
        "# Research Notes\nintro\n\n## Retrieval\nretrieval evidence\n\n"
        "### Evaluation\nrecall evidence\n",
        encoding="utf-8",
    )

    parsed = await EnhancedMarkdownParser().parse(str(markdown_path))
    evaluation = next(section for section in parsed["sections"] if section["heading"] == "Evaluation")

    assert parsed["file_type"] == "markdown"
    assert evaluation["section_path"] == ["Research Notes", "Retrieval", "Evaluation"]
    assert evaluation["start_line"] == 7


@pytest.mark.asyncio
async def test_pdf_parser_returns_one_based_page_numbers(tmp_path: Path) -> None:
    pdf_path = tmp_path / "notes.pdf"
    document = fitz.open()
    for page_number in (1, 2):
        page = document.new_page()
        page.insert_text((72, 72), f"Evidence page {page_number}")
    document.save(pdf_path)
    document.close()

    parsed = await PDFParser().parse(str(pdf_path))

    assert parsed["file_type"] == "pdf"
    assert [page["page_num"] for page in parsed["pages"]] == [1, 2]
