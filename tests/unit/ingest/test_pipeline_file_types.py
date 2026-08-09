import pytest

pytest.importorskip("fitz")
pytest.importorskip("pymilvus")

from src.core.ingest.markdown_chunker import MarkdownChunker
from src.core.ingest.pdf_chunker import PDFChunker
from src.core.ingest.pipeline import DocumentIngestPipeline


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("filename", "expected_type", "expected_chunker"),
    [
        ("paper.pdf", "pdf", PDFChunker),
        ("notes.md", "markdown", MarkdownChunker),
        ("notes.markdown", "markdown", MarkdownChunker),
    ],
)
def test_pipeline_selects_the_matching_chunker(
    filename: str, expected_type: str, expected_chunker: type,
) -> None:
    pipeline = DocumentIngestPipeline()

    file_type = pipeline._file_type(filename)

    assert file_type == expected_type
    assert isinstance(pipeline._get_chunker(file_type), expected_chunker)


def test_pipeline_rejects_unsupported_file_type() -> None:
    with pytest.raises(ValueError, match="不支持的文件类型"):
        DocumentIngestPipeline._file_type("source.txt")
