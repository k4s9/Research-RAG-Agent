import re
from typing import Dict, Any, List, Optional, Tuple
from loguru import logger

from src.core.ingest.base import BaseParser


class MarkdownSection:
    def __init__(
        self,
        level: int,
        heading: str,
        content: str,
        start_line: int,
        end_line: int,
        section_path: Optional[List[str]] = None,
    ):
        self.level = level
        self.heading = heading.strip()
        self.content = content.strip()
        self.start_line = start_line
        self.end_line = end_line
        self.section_path = section_path or ([self.heading] if self.heading else [])
        self.subsections: List[MarkdownSection] = []

    @property
    def word_count(self) -> int:
        return len(self.content.split())

    @property
    def char_count(self) -> int:
        return len(self.content)


class MarkdownBlock:
    BLOCK_TYPES = ["paragraph", "heading", "code_block", "inline_code", "list", "table", "blockquote", "horizontal_rule"]

    def __init__(self, block_type: str, content: str, metadata: Optional[Dict[str, Any]] = None):
        self.type = block_type
        self.content = content
        self.metadata = metadata or {}

    @property
    def word_count(self) -> int:
        return len(self.content.split())


class EnhancedMarkdownParser(BaseParser):
    def __init__(self, min_heading_level: int = 1, max_heading_level: int = 6):
        self.min_heading_level = min_heading_level
        self.max_heading_level = max_heading_level
        self._code_block_pattern = re.compile(r'^```(\w*)\s*$')
        self._heading_pattern = re.compile(r'^(#{1,6})\s+(.+)$')
        self._table_row_pattern = re.compile(r'^\|.*\|$')
        self._list_pattern = re.compile(r'^(\s*)[-*+]\s+(.+)$')
        self._ordered_list_pattern = re.compile(r'^(\s*)\d+\.\s+(.+)$')
        self._blockquote_pattern = re.compile(r'^>\s*(.*)$')
        self._hr_pattern = re.compile(r'^[-*_]{3,}\s*$')

    @property
    def file_type(self) -> str:
        return "markdown"

    async def parse(self, file_path: str) -> Dict[str, Any]:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            lines = content.split('\n')
            sections = self._split_into_sections(lines)
            blocks = self._extract_blocks(lines)
            structure = self._analyze_structure(sections)

            result = {
                "file_type": "markdown",
                "sections": [self._section_to_dict(s) for s in sections],
                "blocks": [self._block_to_dict(b) for b in blocks],
                "structure": structure,
                "total_sections": len(sections),
                "total_blocks": len(blocks),
                "total_words": sum(s.word_count for s in sections)
            }

            logger.info(f"Markdown 解析完成: {file_path}, 章节数: {len(sections)}, 块数: {len(blocks)}")
            return result

        except Exception as e:
            logger.error(f"Markdown 解析失败: {str(e)}")
            raise

    def _split_into_sections(self, lines: List[str]) -> List[MarkdownSection]:
        sections: List[MarkdownSection] = []
        heading_stack: List[Tuple[int, str]] = []
        current_section: Optional[MarkdownSection] = None
        current_content_lines: List[str] = []

        def finish_section(end_line: int) -> None:
            nonlocal current_section, current_content_lines
            if current_section is None:
                return
            current_section.content = "\n".join(current_content_lines).strip()
            current_section.end_line = max(current_section.start_line, end_line)
            if current_section.content or current_section.heading:
                sections.append(current_section)
            current_section = None
            current_content_lines = []

        for index, line in enumerate(lines):
            line_number = index + 1
            heading_match = self._heading_pattern.match(line)
            if not heading_match:
                if current_section is None:
                    current_section = MarkdownSection(
                        level=0,
                        heading="Document Root",
                        content="",
                        start_line=1,
                        end_line=line_number,
                        section_path=[],
                    )
                current_content_lines.append(line)
                continue

            level = len(heading_match.group(1))
            if level < self.min_heading_level or level > self.max_heading_level:
                if current_section is not None:
                    current_content_lines.append(line)
                continue

            finish_section(line_number - 1)
            heading = heading_match.group(2).strip()
            heading_stack = [item for item in heading_stack if item[0] < level]
            heading_stack.append((level, heading))
            current_section = MarkdownSection(
                level=level,
                heading=heading,
                content="",
                start_line=line_number,
                end_line=line_number,
                section_path=[item[1] for item in heading_stack],
            )

        finish_section(len(lines))
        return sections

    def _extract_blocks(self, lines: List[str]) -> List[MarkdownBlock]:
        blocks = []
        i = 0

        while i < len(lines):
            line = lines[i]

            if self._code_block_pattern.match(line.strip()):
                code_lines = [line]
                i += 1
                while i < len(lines) and not lines[i].strip().startswith('```'):
                    code_lines.append(lines[i])
                    i += 1
                if i < len(lines):
                    code_lines.append(lines[i])
                    i += 1

                lang = self._code_block_pattern.match(lines[i - len(code_lines)].strip()).group(1) if lines[i - len(code_lines)].strip() else ""
                blocks.append(MarkdownBlock(
                    block_type="code_block",
                    content='\n'.join(code_lines[1:-1]),
                    metadata={"language": lang, "line_count": len(code_lines)}
                ))

            elif self._table_row_pattern.match(line.strip()):
                table_lines = [line]
                i += 1
                while i < len(lines) and self._table_row_pattern.match(lines[i].strip()):
                    table_lines.append(lines[i])
                    i += 1

                is_header = True
                for t_line in table_lines:
                    if re.match(r'^\|[\s:-]+\|$', t_line.strip()):
                        is_header = False
                        break

                blocks.append(MarkdownBlock(
                    block_type="table",
                    content='\n'.join(table_lines),
                    metadata={"row_count": len(table_lines), "has_header": is_header}
                ))

            elif self._heading_pattern.match(line):
                heading_match = self._heading_pattern.match(line)
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2)
                blocks.append(MarkdownBlock(
                    block_type="heading",
                    content=heading_text,
                    metadata={"level": level}
                ))
                i += 1

            elif self._blockquote_pattern.match(line):
                quote_lines = [self._blockquote_pattern.match(line).group(1)]
                i += 1
                while i < len(lines) and self._blockquote_pattern.match(lines[i]):
                    quote_lines.append(self._blockquote_pattern.match(lines[i]).group(1))
                    i += 1
                blocks.append(MarkdownBlock(
                    block_type="blockquote",
                    content='\n'.join(quote_lines)
                ))

            elif self._hr_pattern.match(line.strip()):
                blocks.append(MarkdownBlock(
                    block_type="horizontal_rule",
                    content=line.strip()
                ))
                i += 1

            elif line.strip():
                para_lines = [line]
                i += 1
                while i < len(lines) and lines[i].strip() and not self._heading_pattern.match(lines[i]):
                    para_lines.append(lines[i])
                    i += 1

                content = '\n'.join(para_lines)
                content_type = self._detect_inline_type(content)

                blocks.append(MarkdownBlock(
                    block_type="paragraph" if content_type == "text" else content_type,
                    content=content
                ))

            else:
                i += 1

        return blocks

    def _detect_inline_type(self, content: str) -> str:
        if re.search(r'`[^`]+`', content):
            return "inline_code"
        if re.search(r'\[[^\]]+\]\([^)]+\)', content):
            return "link"
        if re.search(r'!\[[^\]]*\]\([^)]+\)', content):
            return "image"
        if re.match(r'^[-*+]\s+', content.strip()) or re.match(r'^\d+\.\s+', content.strip()):
            return "list"
        return "text"

    def _analyze_structure(self, sections: List[MarkdownSection]) -> Dict[str, Any]:
        heading_counts = {i: 0 for i in range(1, 7)}
        heading_counts[0] = 0

        for section in sections:
            heading_counts[section.level] = heading_counts.get(section.level, 0) + 1

        return {
            "total_headings": sum(heading_counts.values()),
            "heading_distribution": heading_counts,
            "h1_count": heading_counts.get(1, 0),
            "h2_count": heading_counts.get(2, 0),
            "h3_plus_count": sum(v for k, v in heading_counts.items() if k >= 3),
            "avg_section_length": sum(s.word_count for s in sections) / max(len(sections), 1),
            "max_section_length": max((s.word_count for s in sections), default=0),
            "min_section_length": min((s.word_count for s in sections), default=0)
        }

    def _section_to_dict(self, section: MarkdownSection) -> Dict[str, Any]:
        return {
            "level": section.level,
            "heading": section.heading,
            "content": section.content,
            "start_line": section.start_line,
            "end_line": section.end_line,
            "section_path": section.section_path,
            "word_count": section.word_count,
            "char_count": section.char_count
        }

    def _block_to_dict(self, block: MarkdownBlock) -> Dict[str, Any]:
        return {
            "type": block.type,
            "content": block.content,
            "metadata": block.metadata,
            "word_count": block.word_count
        }
