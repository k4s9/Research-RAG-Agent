"""Conservative cleaning: never rebuild reading order or crop body regions."""

import re
from collections import Counter, defaultdict
from typing import Any

from src.core.ingest.base import BaseCleaner

PAGE_NUMBER = re.compile(
    r"(?:\d{1,4}|第\s*\d+\s*页|(?:page|p\.)\s*\d+(?:\s*(?:of|/)\s*\d+)?)",
    re.IGNORECASE,
)


def normalized(text: str) -> str:
    return " ".join(text.split())


class PDFCleaner(BaseCleaner):
    def __init__(
        self,
        header_threshold: float = 0.08,
        footer_threshold: float = 0.92,
        frequency_threshold: float = 0.6,
    ) -> None:
        if not 0 <= header_threshold < footer_threshold <= 1:
            raise ValueError("页眉页脚区域必须满足 0 <= header < footer <= 1")
        if not 0 < frequency_threshold <= 1:
            raise ValueError("重复页面比例必须在 (0, 1] 范围内")
        self.header_threshold = header_threshold
        self.footer_threshold = footer_threshold
        self.frequency_threshold = frequency_threshold

    @staticmethod
    def _lines(page: dict[str, Any]) -> list[dict[str, Any]]:
        # Legacy inputs may only have spans. Uncertain partial-line matches
        # are deliberately retained rather than reconstructed by coordinates.
        return page.get("lines", page.get("blocks", []))

    def _region(self, line: dict[str, Any], page: dict[str, Any]) -> str | None:
        bbox = line.get("bbox")
        height = page.get("height", 0)
        if not bbox or len(bbox) != 4 or height <= 0:
            return None
        if bbox[3] <= height * self.header_threshold:
            return "header"
        if bbox[1] >= height * self.footer_threshold:
            return "footer"
        return None

    def clean(self, parsed_content: dict[str, Any]) -> dict[str, Any]:
        if parsed_content.get("file_type") == "markdown":
            return parsed_content
        pages = parsed_content.get("pages", [])
        occurrences: dict[tuple[str, str], set[int]] = defaultdict(set)
        for index, page in enumerate(pages):
            for line in self._lines(page):
                region = self._region(line, page)
                text = normalized(line.get("text", ""))
                if region and text:
                    occurrences[(region, text)].add(index)
        repeated = {
            key for key, page_indices in occurrences.items()
            if len(page_indices) >= 2 and len(page_indices) / len(pages) >= self.frequency_threshold
        }
        cleaned_pages = [self._clean_page(page, repeated) for page in pages]
        return {
            **parsed_content,
            "pages": cleaned_pages,
            "filtered_headers": sorted(text for region, text in repeated if region == "header"),
            "filtered_footers": sorted(text for region, text in repeated if region == "footer"),
            "cleaning_applied": True,
        }

    def _clean_page(
        self, page: dict[str, Any], repeated: set[tuple[str, str]],
    ) -> dict[str, Any]:
        original = page.get("text", "")
        text_lines = original.splitlines(keepends=True)
        text_counts = Counter(normalized(line) for line in text_lines)
        geometry_counts = Counter(normalized(line.get("text", "")) for line in self._lines(page))
        removable = {}
        warnings = list(page.get("warnings", []))
        for line in self._lines(page):
            region = self._region(line, page)
            text = normalized(line.get("text", ""))
            if not region or not text:
                continue
            is_page_number = PAGE_NUMBER.fullmatch(text) is not None
            if not is_page_number and (region, text) not in repeated:
                continue
            # sort=True does not provide an exact coordinate map. Only remove
            # an unambiguous entire line; a duplicate in the body stays.
            if text_counts[text] != 1 or geometry_counts[text] != 1:
                warnings.append("ambiguous_margin_text_retained")
                continue
            removable[text] = {
                "text": line["text"],
                "bbox": list(line["bbox"]),
                "line_id": line.get("id"),
                "reason": "page_number" if is_page_number else f"repeated_{region}",
            }

        cleaned = "".join(line for line in text_lines if normalized(line) not in removable)
        return {
            **page,
            "raw_text": original,
            "text": cleaned,
            "warnings": list(dict.fromkeys(warnings)),
            "cleaning": {
                "original_chars": len(original),
                "cleaned_chars": len(cleaned),
                "removed_lines": list(removable.values()),
            },
        }
