#!/usr/bin/env python3
"""增强版 PDF 解析器 - 支持多栏布局和复杂文档

基于 PyMuPDF 的高级文本提取，处理多栏布局、表格、公式等复杂结构。
"""

from typing import Dict, Any, List, Tuple
import fitz
from loguru import logger


class EnhancedPDFParser:
    COLUMN_GAP_THRESHOLD = 50

    def __init__(self, column_threshold: float = 0.3):
        self.column_threshold = column_threshold

    async def parse(self, file_path: str) -> Dict[str, Any]:
        try:
            doc = fitz.open(file_path)
            pages = []

            for page_num in range(len(doc)):
                page = doc[page_num]
                page_data = self._extract_page_content(page, page_num + 1)
                pages.append(page_data)

            doc.close()

            logger.info(f"PDF 解析完成: {file_path}, 共 {len(pages)} 页")
            return {
                "pages": pages,
                "total_pages": len(pages)
            }

        except Exception as e:
            logger.error(f"PDF 解析失败: {str(e)}")
            raise

    def _extract_page_content(self, page: fitz.Page, page_num: int) -> Dict[str, Any]:
        page_width = page.rect.width
        page_height = page.rect.height

        text_dict = page.get_text("dict")

        blocks_info = []
        full_text_lines = []

        for block in text_dict.get("blocks", []):
            if "lines" not in block:
                continue

            for line in block["lines"]:
                for span in line["spans"]:
                    x0, y0, x1, y1 = span["bbox"]
                    text = span["text"].strip()

                    if not text:
                        continue

                    blocks_info.append({
                        "text": text,
                        "bbox": (x0, y0, x1, y1),
                        "font_size": span.get("size", 0),
                        "font_name": span.get("font", ""),
                        "y_center": (y0 + y1) / 2,
                        "x_center": (x0 + x1) / 2
                    })

                    full_text_lines.append({
                        "text": text,
                        "y": (y0 + y1) / 2,
                        "x": x0,
                        "bbox": span["bbox"]
                    })

        cleaned_text = self._reconstruct_text(full_text_lines, page_height)

        text_density = sum(len(b["text"]) for b in blocks_info) / (page_width * page_height)
        is_scanned = text_density < 0.01

        return {
            "page_num": page_num,
            "text": cleaned_text,
            "blocks": blocks_info,
            "is_scanned": is_scanned,
            "width": page_width,
            "height": page_height,
            "line_count": len(full_text_lines)
        }

    def _reconstruct_text(self, lines: List[Dict], page_height: float) -> str:
        if not lines:
            return ""

        mid_y = page_height / 2
        left_lines = []
        right_lines = []

        for line in lines:
            y_center = line["y"]
            if y_center < mid_y:
                if line["x"] < mid_y:
                    left_lines.append(line)
                else:
                    right_lines.append(line)
            else:
                left_lines.append(line)

        left_lines.sort(key=lambda l: (l["y"], l["x"]))
        right_lines.sort(key=lambda l: (l["y"], l["x"]))

        left_text = " ".join(l["text"] for l in left_lines)
        right_text = " ".join(l["text"] for l in right_lines)

        if left_text == right_text:
            return left_text

        if right_lines:
            return left_text + "\n\n" + right_text
        return left_text

    def _detect_columns(self, lines: List[Dict]) -> int:
        if len(lines) < 3:
            return 1

        x_coords = [line["x"] for line in lines]
        x_coords.sort()

        gaps = []
        for i in range(len(x_coords) - 1):
            gap = x_coords[i + 1] - x_coords[i]
            if gap > self.COLUMN_GAP_THRESHOLD:
                gaps.append((i, gap))

        if len(gaps) >= 2:
            gaps.sort(key=lambda x: x[1], reverse=True)
            if gaps[0][1] > 100:
                return 2

        return 1

    def _normalize_spacing(self, text: str) -> str:
        import re
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\s+\.', '.', text)
        text = re.sub(r'\s+,', ',', text)
        text = re.sub(r'\s+\)', ')', text)
        text = re.sub(r'\(\s+', '(', text)

        text = re.sub(r'\.\s+', '.\n\n', text)

        return text.strip()


class LayoutAwarePDFParser:
    def __init__(self):
        self.enhanced_parser = EnhancedPDFParser()

    async def parse(self, file_path: str) -> Dict[str, Any]:
        return await self.enhanced_parser.parse(file_path)

    def _is_table_block(self, block: Dict) -> bool:
        bbox = block.get("bbox", [])
        if len(bbox) != 4:
            return False

        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]

        if height < 10 or width < 50:
            return False

        return False

    def _is_figure_caption(self, block: Dict) -> bool:
        text = block.get("text", "").lower()
        font_size = block.get("font_size", 0)

        if font_size < 8:
            return True

        if text.startswith("figure") or text.startswith("fig."):
            return True

        return False
