"""Page-local, bounded chunks with one overlap and lossless source slices."""

import re
from typing import Any

from src.core.ingest.base import BaseChunker, ChunkResult
from src.core.ingest.text_budget import LocalTokenizerBudget, TextBudget, UTF8Budget

STRUCTURAL_BOUNDARY = re.compile(r"\n\s*\n|(?<=[。！？])|(?<=[.!?])\s+")
WORD_BOUNDARY = re.compile(r"\s+")


class PDFChunker(BaseChunker):
    def __init__(
        self,
        chunk_size: int = 384,
        overlap: int = 128,
        min_chunk_size: int = 50,
        *,
        budget: TextBudget | None = None,
        tokenizer_path: str | None = None,
    ) -> None:
        if chunk_size <= 0 or not 0 <= overlap < chunk_size or min_chunk_size < 0:
            raise ValueError("必须满足 chunk_size > 0、0 <= overlap < chunk_size、min_chunk_size >= 0")
        if budget is not None and tokenizer_path:
            raise ValueError("budget 和 tokenizer_path 不能同时配置")
        super().__init__(chunk_size, overlap)
        self.min_chunk_size = min_chunk_size
        self._budget = budget
        self.tokenizer_path = tokenizer_path

    @property
    def budget(self) -> TextBudget:
        if self._budget is None:
            self._budget = (
                LocalTokenizerBudget(self.tokenizer_path) if self.tokenizer_path else UTF8Budget()
            )
        return self._budget

    def chunk(self, cleaned_content: dict[str, Any]) -> list[ChunkResult]:
        if cleaned_content.get("file_type") != "pdf":
            raise ValueError("PDFChunker 只接受 file_type=pdf 的内容")
        chunks = []
        for index, page in enumerate(cleaned_content.get("pages", [])):
            chunks.extend(self._chunk_page(page, page.get("page_num", index + 1)))
        return chunks

    def _prefix_end(self, text: str, start: int) -> int:
        low, high = start, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if self.budget.count(text[start:middle]) <= self.chunk_size:
                low = middle
            else:
                high = middle - 1
        # Token counts need not be monotonic. A shorter slice is acceptable;
        # an unchecked or oversized slice is not.
        if low == start or self.budget.count(text[start:low]) > self.chunk_size:
            raise ValueError("PDF 分块预算过小，无法容纳下一个字符及 tokenizer 特殊标记")
        return low

    def _boundary_end(self, text: str, start: int, end: int, previous_end: int) -> int:
        if end == len(text):
            return end
        minimum = min(self.min_chunk_size, self.chunk_size // 2)
        for pattern in (STRUCTURAL_BOUNDARY, WORD_BOUNDARY):
            candidates = [start + match.end() for match in pattern.finditer(text[start:end])]
            for candidate in reversed(candidates):
                if candidate <= previous_end:
                    continue
                count = self.budget.count(text[start:candidate])
                if minimum <= count <= self.chunk_size:
                    return candidate
        return end

    def _overlap_start(self, text: str, start: int, end: int) -> int:
        if not self.overlap:
            return end
        low, high = start, end
        while low < high:
            middle = (low + high) // 2
            if self.budget.count(text[middle:end]) <= self.overlap:
                high = middle
            else:
                low = middle + 1
        return low if self.budget.count(text[low:end]) <= self.overlap else end

    def _chunk_page(self, page: dict[str, Any], page_num: int) -> list[ChunkResult]:
        text = page.get("text", "")
        chunks = []
        start = previous_end = 0
        while start < len(text):
            while start < len(text) and text[start].isspace():
                start += 1
            if start == len(text):
                break
            end = self._prefix_end(text, start)
            if end <= previous_end:
                # Do not emit overlap-only chunks or stop making progress.
                start = previous_end
                continue
            end = self._boundary_end(text, start, end, previous_end)
            while end > start and text[end - 1].isspace():
                end -= 1
            if end <= previous_end:
                start = previous_end
                continue
            content = text[start:end]
            count = self.budget.count(content)
            if count > self.chunk_size:
                raise ValueError("PDF 分块在规范化后超过预算")
            overlap_chars = max(0, previous_end - start)
            metadata = {
                "page_num": page_num,
                "page_start": page_num,
                "page_end": page_num,
                "chunk_type": "bounded_page",
                "chunker_version": "pdf-bounded-v2",
                "budget_unit": self.budget.unit,
                "budget_count": count,
                "budget_limit": self.chunk_size,
                "tokenizer_id": self.budget.identity,
                "has_overlap": overlap_chars > 0,
                "overlap_chars": overlap_chars,
                "char_start": start,
                "char_end": end,
                "body_char_start": max(start, previous_end),
                "offset_basis": "cleaned_page_text",
                "is_scanned": page.get("is_scanned", False),
            }
            if self.budget.unit == "tokens":
                metadata["token_count"] = count
            chunks.append(ChunkResult(content=content, content_type="text", metadata=metadata))
            if end == len(text) or not text[end:].strip():
                break
            start = self._overlap_start(text, start, end)
            previous_end = end
        return chunks
