from typing import Dict, Any, List, Set
import re
from loguru import logger

from src.core.ingest.base import BaseCleaner


class PDFCleaner(BaseCleaner):
    def __init__(
        self,
        header_threshold: float = 0.25,
        footer_threshold: float = 0.80,
        frequency_threshold: float = 0.6,
        text_quality_threshold: float = 0.5
    ):
        self.header_threshold = header_threshold
        self.footer_threshold = footer_threshold
        self.frequency_threshold = frequency_threshold
        self.text_quality_threshold = text_quality_threshold

    def clean(self, parsed_content: Dict[str, Any]) -> Dict[str, Any]:
        if parsed_content.get("file_type") == "markdown":
            logger.warning("PDFCleaner 收到了 Markdown 内容，应使用 MarkdownCleaner")
            return parsed_content

        pages = parsed_content.get("pages", [])
        cleaned_pages = []

        header_candidates = []
        footer_candidates = []

        for page in pages:
            blocks = page.get("blocks", [])
            page_height = page.get("height", 1000)

            header_region = page_height * self.header_threshold
            footer_region = page_height * self.footer_threshold

            for block in blocks:
                bbox = block.get("bbox", [0, 0, 0, 0])
                y0, y1 = bbox[1], bbox[3]

                if y1 <= header_region:
                    header_candidates.append(block.get("text", ""))
                elif y0 >= footer_region:
                    footer_candidates.append(block.get("text", ""))

        headers = self._detect_repeated_text(header_candidates, len(pages))
        footers = self._detect_repeated_text(footer_candidates, len(pages))

        filtered_headers = self._filter_page_numbers(headers)
        filtered_footers = self._filter_page_numbers(footers)

        for page in pages:
            cleaned_page = self._clean_page(page, filtered_headers, filtered_footers)
            cleaned_pages.append(cleaned_page)

        result = parsed_content.copy()
        result["pages"] = cleaned_pages
        result["filtered_headers"] = filtered_headers
        result["filtered_footers"] = filtered_footers
        result["cleaning_applied"] = True

        logger.info(f"PDF 清洗完成: 过滤了 {len(filtered_headers)} 个页眉和 {len(filtered_footers)} 个页脚")
        return result

    def _filter_page_numbers(self, candidates: List[str]) -> List[str]:
        page_number_pattern = re.compile(r'^\d+$|^\d+\s*$|^第\s*\d+\s*页$|^Page\s*\d+$|^p\.\s*\d+$', re.IGNORECASE)
        filtered = []
        for text in candidates:
            if page_number_pattern.match(text.strip()):
                continue
            if len(text.strip()) <= 2:
                continue
            filtered.append(text)
        return filtered

    def _detect_repeated_text(self, candidates: List[str], total_pages: int) -> List[str]:
        text_counts = {}
        for text in candidates:
            if text.strip() and len(text.strip()) > 2:
                text_counts[text] = text_counts.get(text, 0) + 1

        threshold = total_pages * self.frequency_threshold
        return [text for text, count in text_counts.items() if count > threshold]

    def _is_text_quality_good(self, text: str) -> bool:
        if not text:
            return False

        alpha_chars = sum(1 for c in text if c.isalpha())
        total_chars = len(text.replace(" ", "").replace("\n", ""))

        if total_chars == 0:
            return False

        alpha_ratio = alpha_chars / total_chars

        words = text.split()
        if len(words) < 3:
            return False

        return alpha_ratio >= self.text_quality_threshold

    def _clean_page(self, page: Dict[str, Any], headers: List[str], footers: List[str]) -> Dict[str, Any]:
        blocks = page.get("blocks", [])
        original_text = page.get("text", "")
        page_height = page.get("height", 800)

        cleaned_page = page.copy()

        if self._is_text_quality_good(original_text):
            cleaned_text = self._remove_headers_footers_from_text(
                original_text, headers, footers, page_height
            )
            cleaned_page["text"] = cleaned_text
        else:
            cleaned_blocks = self._filter_blocks_by_position(
                blocks, headers, footers, page_height
            )
            cleaned_text = self._build_text_from_blocks(cleaned_blocks)
            cleaned_page["text"] = cleaned_text if cleaned_text else original_text
            cleaned_page["blocks"] = cleaned_blocks

        return cleaned_page

    def _remove_headers_footers_from_text(
        self,
        text: str,
        headers: List[str],
        footers: List[str],
        page_height: float
    ) -> str:
        lines = text.split("\n")
        cleaned_lines = []

        for line in lines:
            line_stripped = line.strip()
            line_len = len(line_stripped)

            is_removable_header = False
            for header in headers:
                header_stripped = header.strip()
                header_len = len(header_stripped)
                if header_len < 3:
                    continue
                if line_len > 0 and len(line_stripped) < len(header_stripped) * 1.5:
                    if header_stripped == line_stripped[:header_len]:
                        is_removable_header = True
                        break

            is_removable_footer = False
            if not is_removable_header:
                for footer in footers:
                    footer_stripped = footer.strip()
                    footer_len = len(footer_stripped)
                    if footer_len < 3:
                        continue
                    if line_len > 0 and len(line_stripped) < len(footer_stripped) * 1.5:
                        if footer_stripped == line_stripped[:footer_len]:
                            is_removable_footer = True
                            break

            if not is_removable_header and not is_removable_footer:
                cleaned_lines.append(line)

        return "\n".join(cleaned_lines)

    def _filter_blocks_by_position(
        self,
        blocks: List[Dict],
        headers: List[str],
        footers: List[str],
        page_height: float
    ) -> List[Dict]:
        header_region = page_height * self.header_threshold
        footer_region = page_height * self.footer_threshold

        filtered_blocks = []

        for block in blocks:
            bbox = block.get("bbox", [0, 0, 0, 0])
            y0, y1 = bbox[1], bbox[3]
            block_text = block.get("text", "").strip()

            if not block_text:
                continue

            if y1 <= header_region or y0 >= footer_region:
                continue

            is_filtered = False
            for header in headers:
                if header.strip() in block_text:
                    is_filtered = True
                    break

            if not is_filtered:
                for footer in footers:
                    if footer.strip() in block_text:
                        is_filtered = True
                        break

            if not is_filtered:
                filtered_blocks.append(block)

        return filtered_blocks

    def _build_text_from_blocks(self, blocks: List[Dict]) -> str:
        if not blocks:
            return ""

        sorted_blocks = sorted(blocks, key=lambda b: (
            b.get("bbox", [0, 0, 0, 0])[1],
            b.get("bbox", [0, 0, 0, 0])[0]
        ))

        lines = []
        current_line_blocks = []
        current_y = None
        line_gap_threshold = 10

        for block in sorted_blocks:
            bbox = block.get("bbox", [0, 0, 0, 0])
            block_y0 = bbox[1]
            block_text = block.get("text", "").strip()

            if not block_text:
                continue

            if current_y is None:
                current_y = block_y0
                current_line_blocks = [block]
            elif abs(block_y0 - current_y) <= line_gap_threshold:
                current_line_blocks.append(block)
                current_y = (current_y + block_y0) / 2
            else:
                line_text = self._merge_blocks_to_line(current_line_blocks)
                if line_text:
                    lines.append(line_text)
                current_line_blocks = [block]
                current_y = block_y0

        if current_line_blocks:
            line_text = self._merge_blocks_to_line(current_line_blocks)
            if line_text:
                lines.append(line_text)

        return "\n".join(lines)

    def _merge_blocks_to_line(self, blocks: List[Dict]) -> str:
        if not blocks:
            return ""

        sorted_line_blocks = sorted(blocks, key=lambda b: b.get("bbox", [0, 0, 0, 0])[0])

        parts = []
        for block in sorted_line_blocks:
            text = block.get("text", "").strip()
            if text:
                parts.append(text)

        return " ".join(parts)
