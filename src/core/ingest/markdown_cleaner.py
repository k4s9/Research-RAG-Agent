import re
from typing import Dict, Any, List, Set
from loguru import logger

from src.core.ingest.base import BaseCleaner


class MarkdownCleaner(BaseCleaner):
    def __init__(self, preserve_structure: bool = True):
        self.preserve_structure = preserve_structure
        self._url_pattern = re.compile(r'\[([^\]]+)\]\([^)]+\)')
        self._image_pattern = re.compile(r'!\[[^\]]*\]\([^)]+\)')
        self._bold_pattern = re.compile(r'\*\*([^*]+)\*\*')
        self._italic_pattern = re.compile(r'\*([^*]+)\*')
        self._inline_code_pattern = re.compile(r'`([^`]+)`')
        self._heading_pattern = re.compile(r'^#{1,6}\s+', re.MULTILINE)

    def clean(self, parsed_content: Dict[str, Any]) -> Dict[str, Any]:
        if parsed_content.get("file_type") != "markdown":
            logger.warning(f"MarkdownCleaner 收到了非 Markdown 内容: {parsed_content.get('file_type', 'unknown')}")
            return parsed_content

        cleaned_sections = []
        cleaned_blocks = []

        for section in parsed_content.get("sections", []):
            cleaned_section = self._clean_section(section)
            cleaned_sections.append(cleaned_section)

        for block in parsed_content.get("blocks", []):
            cleaned_block = self._clean_block(block)
            cleaned_blocks.append(cleaned_block)

        result = parsed_content.copy()
        result["sections"] = cleaned_sections
        result["blocks"] = cleaned_blocks
        result["cleaning_applied"] = True

        logger.info(f"Markdown 清洗完成: {len(cleaned_sections)} 个章节, {len(cleaned_blocks)} 个块")
        return result

    def _clean_section(self, section: Dict[str, Any]) -> Dict[str, Any]:
        content = section.get("content", "")

        content = self._normalize_whitespace(content)
        content = self._remove_excess_blank_lines(content)

        cleaned = section.copy()
        cleaned["content"] = content
        cleaned["original_length"] = len(section.get("content", ""))
        cleaned["cleaned_length"] = len(content)
        cleaned["removed_chars"] = cleaned["original_length"] - cleaned["cleaned_length"]

        return cleaned

    def _clean_block(self, block: Dict[str, Any]) -> Dict[str, Any]:
        block_type = block.get("type", "text")
        content = block.get("content", "")

        if block_type == "code_block":
            content = self._clean_code_block(content)
        elif block_type == "table":
            content = self._normalize_table(content)
        elif block_type == "paragraph":
            content = self._normalize_text(content)
        elif block_type == "heading":
            content = self._normalize_heading(content)

        cleaned = block.copy()
        cleaned["content"] = content

        return cleaned

    def _normalize_whitespace(self, text: str) -> str:
        text = text.replace('\r\n', '\n')
        text = re.sub(r'[ \t]+\n', '\n', text)
        text = re.sub(r'\n[ \t]+', '\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()

    def _remove_excess_blank_lines(self, text: str, max_blank_lines: int = 2) -> str:
        return re.sub(r'\n{' + str(max_blank_lines + 1) + r',}', '\n' * max_blank_lines, text)

    def _normalize_text(self, text: str) -> str:
        text = self._normalize_whitespace(text)

        if not self.preserve_structure:
            text = self._remove_markdown_formatting(text)

        return text

    def _normalize_heading(self, text: str) -> str:
        return self._normalize_whitespace(text)

    def _clean_code_block(self, content: str) -> str:
        lines = content.split('\n')
        cleaned_lines = [line.rstrip() for line in lines]
        content = '\n'.join(cleaned_lines)

        return content

    def _normalize_table(self, content: str) -> str:
        lines = content.split('\n')
        normalized_lines = []

        for line in lines:
            line = line.strip()
            if re.match(r'^\|[\s:-]+\|$', line):
                normalized_lines.append('| ' + ' | '.join(['---' for _ in line.split('|')[1:-1]]) + ' |')
            else:
                parts = [p.strip() for p in line.split('|')[1:-1]]
                normalized_lines.append('| ' + ' | '.join(parts) + ' |')

        return '\n'.join(normalized_lines)

    def _remove_markdown_formatting(self, text: str) -> str:
        text = self._url_pattern.sub(r'\1', text)
        text = self._image_pattern.sub('', text)
        text = self._bold_pattern.sub(r'\1', text)
        text = self._italic_pattern.sub(r'\1', text)
        text = self._inline_code_pattern.sub(r'\1', text)

        return text

    def remove_urls(self, text: str) -> str:
        return self._url_pattern.sub('', text)

    def remove_images(self, text: str) -> str:
        return self._image_pattern.sub('', text)

    def keep_only_structure(self, text: str) -> str:
        text = self._heading_pattern.sub('', text)
        text = re.sub(r'^[-*+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
        return text
