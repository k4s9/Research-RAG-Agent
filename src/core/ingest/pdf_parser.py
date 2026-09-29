"""Native PDF extraction with explicit per-page coverage diagnostics."""

from typing import Any

import fitz
from loguru import logger


class PDFParser:
    async def parse(self, file_path: str) -> dict[str, Any]:
        pages = []
        with fitz.open(file_path) as document:
            if document.needs_pass:
                raise ValueError("PDF 已加密，请先提供已解密的文件")
            for page in document:
                text = page.get_text("text", sort=True)
                blocks = []
                lines = []
                for block_index, block in enumerate(page.get_text("dict")["blocks"]):
                    for line_index, line in enumerate(block.get("lines", [])):
                        line_id = f"p{page.number + 1}-b{block_index}-l{line_index}"
                        lines.append({
                            "id": line_id,
                            "text": "".join(span["text"] for span in line["spans"]),
                            "bbox": line["bbox"],
                        })
                        blocks.extend({
                            "text": span["text"],
                            "bbox": span["bbox"],
                            "font_size": span["size"],
                            "line_id": line_id,
                        } for span in line["spans"])

                image_area = sum(
                    (fitz.Rect(item["bbox"]) & page.rect).get_area()
                    for item in page.get_image_info()
                )
                image_coverage = min(1.0, image_area / max(page.rect.get_area(), 1))
                visible = [char for char in text if not char.isspace()]
                bad_chars = sum(char in {"\ufffd", "\x00"} for char in visible)
                warnings = []
                reason = None
                if not visible:
                    if image_coverage:
                        status, reason = "needs_ocr", "image_without_text"
                    elif page.get_drawings():
                        status, reason = "needs_review", "graphics_without_text"
                    else:
                        status = "blank"
                elif bad_chars / len(visible) > 0.1:
                    status, reason = "needs_review", "unreadable_text_layer"
                elif image_coverage >= 0.5 and len(visible) < 100:
                    status, reason = "needs_ocr", "image_with_sparse_text"
                else:
                    status = "ok"
                if image_coverage and status == "ok":
                    warnings.append("image_content_not_interpreted")

                pages.append({
                    "page_num": page.number + 1,
                    "text": text,
                    "blocks": blocks,
                    "lines": lines,
                    "words": [{
                        "text": word[4],
                        "bbox": word[:4],
                        "origin": word[5:7],
                    } for word in page.get_text("words", sort=True)],
                    "is_scanned": status == "needs_ocr",
                    "parse_status": status,
                    "parse_reason": reason,
                    "extraction_method": "native",
                    "image_coverage": image_coverage,
                    "warnings": warnings,
                    "width": page.rect.width,
                    "height": page.rect.height,
                })

        logger.info(f"PDF 原生解析完成: {file_path}, 共 {len(pages)} 页")
        return {"file_type": "pdf", "pages": pages, "total_pages": len(pages)}
