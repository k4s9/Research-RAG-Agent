from typing import Dict, Any, List, Tuple
import fitz
from loguru import logger


class PDFParser:
    async def parse(self, file_path: str) -> Dict[str, Any]:
        try:
            doc = fitz.open(file_path)
            pages = []

            for page_num in range(len(doc)):
                page = doc[page_num]
                rect = page.rect
                page_width = rect.width
                page_height = rect.height

                text = page.get_text("text", sort=True)

                text_density = len(text) / (page_width * page_height)
                is_scanned = text_density < 0.01

                blocks = []
                for block in page.get_text("dict")["blocks"]:
                    if "lines" in block:
                        for line in block["lines"]:
                            for span in line["spans"]:
                                blocks.append({
                                    "text": span["text"],
                                    "bbox": span["bbox"],
                                    "font_size": span["size"]
                                })

                words = page.get_text("words", sort=True)
                word_items = [{
                    "text": w[4],
                    "bbox": (w[0], w[1], w[2], w[3]),
                    "origin": (w[5], w[6]) if len(w) > 6 else (0, 0)
                } for w in words]

                pages.append({
                    "page_num": page_num + 1,
                    "text": text,
                    "blocks": blocks,
                    "words": word_items,
                    "is_scanned": is_scanned,
                    "width": page_width,
                    "height": page_height
                })

            doc.close()

            logger.info(f"PDF 解析完成: {file_path}, 共 {len(pages)} 页")
            return {
                "pages": pages,
                "total_pages": len(pages)
            }

        except Exception as e:
            logger.error(f"PDF 解析失败: {str(e)}")
            raise
