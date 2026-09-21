from collections import defaultdict
from types import SimpleNamespace

import pytest

from src.core.document_view import select_document_range

pytestmark = pytest.mark.unit


def chunk(index: int, content: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"c{index}",
        chunk_index=index,
        content=content,
        content_type="text",
        chunk_metadata={"page_start": 1, "page_end": 1},
    )


@pytest.mark.parametrize(
    "texts, truncated",
    [
        (["x" * 9000], True),
        (["x" * 200], False),
        (["x" * 198, "y"], True),
        (["x" * 197, "y"], False),
    ],
)
def test_strict_text_budget_includes_first_chunk_and_separators(
    texts: list[str],
    truncated: bool,
) -> None:
    originals = [chunk(i, text) for i, text in enumerate(texts)]
    selected, cursor = select_document_range(originals, max_chars=200)
    assert len("\n\n".join(item.content for item in selected)) <= 200
    assert (cursor is not None) is truncated
    assert [item.content for item in originals] == texts


def test_cursor_reads_all_text_without_gaps_or_duplicates() -> None:
    originals = [chunk(0, "0123456789" * 900), chunk(1, "final paragraph")]
    collected = defaultdict(str)
    cursor = {}
    for _ in range(60):
        selected, cursor = select_document_range(originals, max_chars=200, **cursor)
        assert len("\n\n".join(item.content for item in selected)) <= 200
        for item in selected:
            assert item.char_start == len(collected[item.id])
            collected[item.id] += item.content
            assert item.char_end == len(collected[item.id])
        if cursor is None:
            break
    else:
        pytest.fail("range cursor did not reach the end")
    assert dict(collected) == {item.id: item.content for item in originals}
