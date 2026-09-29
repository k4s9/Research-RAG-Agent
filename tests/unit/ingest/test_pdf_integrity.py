import copy
import random
import re
from pathlib import Path

import fitz
import pytest

from src.core.ingest.pdf_chunker import PDFChunker
from src.core.ingest.pdf_cleaner import PDFCleaner
from src.core.ingest.pdf_parser import PDFParser
from src.core.ingest.pdf_quality import pdf_page_results

pytestmark = pytest.mark.unit


def page(text: str, lines: list[dict], number: int = 1) -> dict:
    return {"page_num": number, "text": text, "height": 800, "lines": lines}


def line(text: str, y: int, x: int = 70) -> dict:
    return {"text": text, "bbox": [x, y, x + 150, y + 15]}


def cleaned(pages: list[dict]) -> dict:
    return PDFCleaner().clean({"file_type": "pdf", "pages": pages})


@pytest.mark.asyncio
async def test_native_short_page_is_not_scanned_and_empty_page_is_accounted_for(tmp_path: Path):
    source = tmp_path / "short-and-blank.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), "Evidence.")
        document.new_page()
        document.save(source)
    result = await PDFParser().parse(str(source))
    assert [p["parse_status"] for p in result["pages"]] == ["ok", "blank"]
    assert not result["pages"][0]["is_scanned"]
    assert result["pages"][0]["lines"][0]["text"] == "Evidence."


@pytest.mark.asyncio
async def test_image_page_with_native_caption_still_requires_ocr(tmp_path: Path):
    source = tmp_path / "captioned-scan.pdf"
    with fitz.open() as original:
        original.new_page().insert_text((72, 72), "Evidence inside an image.")
        png = original[0].get_pixmap().tobytes("png")
    with fitz.open() as document:
        scanned = document.new_page()
        scanned.insert_image(scanned.rect, stream=png)
        scanned.insert_text((72, 740), "Caption")
        document.save(source)
    result = await PDFParser().parse(str(source))
    assert result["pages"][0]["parse_status"] == "needs_ocr"
    assert result["pages"][0]["parse_reason"] == "image_with_sparse_text"


def test_cleaning_preserves_chinese_body_margins_and_does_not_mutate_input():
    original = {"file_type": "pdf", "pages": [page(
        "这是重要的研究结论。\n这里保留中部正文。\n这是底部脚注。",
        [line("这是重要的研究结论。", 60), line("这里保留中部正文。", 300), line("这是底部脚注。", 760)],
    )]}
    before = copy.deepcopy(original)
    result = PDFCleaner().clean(original)
    assert result["pages"][0]["text"] == before["pages"][0]["text"]
    assert result["pages"][0]["raw_text"] == before["pages"][0]["text"]
    assert original == before


def test_cleaning_preserves_known_column_order_for_numeric_text():
    source = page("111\n222\n333\n444", [
        line("111", 300), line("222", 330), line("333", 300, 330), line("444", 330, 330),
    ])
    assert cleaned([source])["pages"][0]["text"] == source["text"]


def test_repeated_headers_and_positioned_page_numbers_are_removed_with_audit():
    pages = [page(
        f"Research Header\nBody evidence {i}.\n{i}\n",
        [line("Research Header", 20), line(f"Body evidence {i}.", 300), line(str(i), 765)],
        i,
    ) for i in range(1, 4)]
    result = cleaned(pages)
    for i, item in enumerate(result["pages"], 1):
        assert item["text"] == f"Body evidence {i}.\n"
        assert {entry["reason"] for entry in item["cleaning"]["removed_lines"]} == {
            "repeated_header", "page_number",
        }


def test_same_page_repetitions_do_not_count_as_cross_page_headers():
    pages = [page("Title\nTitle\nTitle\nBody", [line("Title", 10), line("Title", 25), line("Title", 40)])]
    pages.extend(page("Different body", [line("Different body", 300)], n) for n in (2, 3))
    assert cleaned(pages)["pages"][0]["text"] == pages[0]["text"]


def test_header_text_repeated_in_body_is_retained_when_alignment_is_ambiguous():
    pages = [page("Research Header\nBody\nResearch Header", [
        line("Research Header", 20), line("Body", 300), line("Research Header", 500),
    ], n) for n in (1, 2)]
    result = cleaned(pages)
    assert result["pages"][0]["text"] == pages[0]["text"]
    assert "ambiguous_margin_text_retained" in result["pages"][0]["warnings"]


def test_body_numbers_and_partial_header_matches_are_never_deleted():
    pages = [page("Version\n123\nVersion control evidence", [
        line("Version", 20), line("123", 300), line("Version control evidence", 500),
    ], n) for n in (1, 2)]
    assert cleaned(pages)["pages"][0]["text"] == "123\nVersion control evidence"


@pytest.mark.parametrize("text", [
    "中文无空格正文。" * 400,
    "one two three. " + " ".join(f"word{i}" for i in range(100)) + ".",
    "🧪e\u0301中文 😀\n\nnext paragraph " * 15,
])
@pytest.mark.parametrize("scanned", [False, True])
@pytest.mark.parametrize(("size", "overlap"), [(12, 0), (64, 16), (12, 11)])
def test_chunk_bounds_and_novel_text_coverage(text: str, scanned: bool, size: int, overlap: int):
    chunks = PDFChunker(chunk_size=size, overlap=overlap).chunk({
        "file_type": "pdf", "pages": [{"page_num": 2, "text": text, "is_scanned": scanned}],
    })
    novel = []
    previous_end = 0
    for chunk in chunks:
        meta = chunk.metadata
        assert chunk.content == text[meta["char_start"]:meta["char_end"]]
        assert len(chunk.content.encode("utf-8")) <= size
        assert meta["budget_unit"] == "utf8_bytes"
        assert "token_count" not in meta
        assert meta["page_start"] == meta["page_end"] == 2
        assert meta["char_end"] > previous_end
        assert len(text[meta["char_start"]:previous_end].encode("utf-8")) <= overlap
        novel.append(text[meta["body_char_start"]:meta["char_end"]])
        previous_end = meta["char_end"]
    assert re.sub(r"\s", "", "".join(novel)) == re.sub(r"\s", "", text)


def test_scanned_and_native_text_share_one_overlap_algorithm():
    text = " ".join(f"w{i:02}" for i in range(20))
    results = [PDFChunker(chunk_size=31, overlap=7).chunk({
        "file_type": "pdf", "pages": [{"page_num": 1, "text": text, "is_scanned": scanned}],
    }) for scanned in (False, True)]
    assert [c.content for c in results[0]] == [c.content for c in results[1]]
    for chunk in results[0]:
        words = chunk.content.split()
        assert len(words) == len(set(words))


def test_overlap_never_crosses_pages_and_tail_is_preserved():
    chunks = PDFChunker(chunk_size=12, overlap=5).chunk({"file_type": "pdf", "pages": [
        {"page_num": 1, "text": "first page evidence"},
        {"page_num": 2, "text": "尾"},
    ]})
    assert chunks[-1].content == "尾"
    assert not chunks[-1].metadata["has_overlap"]


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (-1, 0), (8, 8), (8, 9), (8, -1)])
def test_invalid_chunk_budget_fails_immediately(size: int, overlap: int):
    with pytest.raises(ValueError):
        PDFChunker(chunk_size=size, overlap=overlap)


def test_budget_too_small_for_unicode_character_fails_instead_of_dropping_it():
    with pytest.raises(ValueError, match="预算过小"):
        PDFChunker(chunk_size=2, overlap=0).chunk({
            "file_type": "pdf", "pages": [{"page_num": 1, "text": "中"}],
        })


def test_injected_token_counter_includes_framing_and_overlap():
    class FramedCharacterBudget:
        unit = "tokens"
        identity = "test-codepoints-with-two-special-tokens"

        def count(self, text):
            return len(text) + 2

    source = "中文与English混合。" * 30
    chunks = PDFChunker(chunk_size=12, overlap=4, budget=FramedCharacterBudget()).chunk({
        "file_type": "pdf", "pages": [{"page_num": 1, "text": source}],
    })
    assert len(chunks) > 1
    assert all(c.metadata["token_count"] == len(c.content) + 2 <= 12 for c in chunks)


def test_randomized_unicode_slices_make_progress_without_losing_evidence():
    rng = random.Random(260926)
    for _ in range(50):
        source = "".join(rng.choice("a中1。\n 🙂\t") for _ in range(100))
        size = rng.randint(5, 40)
        chunks = PDFChunker(chunk_size=size, overlap=rng.randrange(size)).chunk({
            "file_type": "pdf", "pages": [{"page_num": 1, "text": source}],
        })
        novel = "".join(source[c.metadata["body_char_start"]:c.metadata["char_end"]] for c in chunks)
        assert re.sub(r"\s", "", novel) == re.sub(r"\s", "", source)
        assert all(len(c.content.encode("utf-8")) <= size for c in chunks)


def test_quality_gate_does_not_assume_an_unclassified_empty_page_is_blank():
    parsed = {"pages": [{"page_num": 1, "text": ""}]}
    assert pdf_page_results(parsed, parsed)[0]["status"] == "needs_review"


def test_quality_gate_detects_cleaner_removing_a_page():
    parsed = {"pages": [{"page_num": 1, "text": "Evidence", "parse_status": "ok"}]}
    result = pdf_page_results(parsed, {"pages": []})
    assert result[0]["status"] == "needs_review"
    assert result[0]["reason"] == "empty_after_cleaning"
