from typing import Dict, Any, List
from loguru import logger

from src.core.ingest.base import BaseCleaner


class DocumentCleaner(BaseCleaner):
    def __init__(self):
        self._markdown_cleaner = None
        self._pdf_cleaner = None

    def clean(self, parsed_content: Dict[str, Any]) -> Dict[str, Any]:
        file_type = parsed_content.get("file_type", "")

        if file_type == "markdown":
            if self._markdown_cleaner is None:
                from src.core.ingest.markdown_cleaner import MarkdownCleaner
                self._markdown_cleaner = MarkdownCleaner()
            return self._markdown_cleaner.clean(parsed_content)

        elif file_type in ["pdf", ""]:
            if self._pdf_cleaner is None:
                from src.core.ingest.pdf_cleaner import PDFCleaner
                self._pdf_cleaner = PDFCleaner()
            return self._pdf_cleaner.clean(parsed_content)

        else:
            logger.warning(f"未知的文件类型: {file_type}，使用默认清洗")
            return parsed_content
