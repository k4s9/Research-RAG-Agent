from typing import Dict, Any, List, Optional
import re
from loguru import logger

from src.core.ingest.base import BaseChunker, ChunkResult


class MarkdownChunker(BaseChunker):
    def __init__(
        self,
        chunk_size: int = 384,
        overlap: int = 128,
        min_chunk_size: int = 100,
        max_chunk_size: int = 384,
        preserve_headings: bool = True,
        split_by_heading: bool = True
    ):
        super().__init__(chunk_size, overlap)
        self.min_chunk_size = min_chunk_size
        self.max_chunk_size = max_chunk_size
        self.preserve_headings = preserve_headings
        self.split_by_heading = split_by_heading

    def chunk(self, cleaned_content: Dict[str, Any]) -> List[ChunkResult]:
        if cleaned_content.get("file_type") != "markdown":
            logger.warning(f"MarkdownChunker 收到了非 Markdown 内容")
            return self._fallback_chunk(cleaned_content)

        chunks = []

        if self.split_by_heading:
            chunks.extend(self._chunk_by_heading(cleaned_content))
        else:
            chunks.extend(self._chunk_by_paragraph(cleaned_content))

        chunks = self._ensure_chunk_quality(chunks)

        logger.info(f"Markdown 分块完成: 生成 {len(chunks)} 个 chunks")
        return chunks

    def _chunk_by_heading(self, content: Dict[str, Any]) -> List[ChunkResult]:
        chunks = []
        sections = content.get("sections", [])

        for i, section in enumerate(sections):
            section_chunks = self._chunk_section(section, i)
            chunks.extend(section_chunks)

        return chunks

    def _chunk_section(self, section: Dict[str, Any], section_index: int) -> List[ChunkResult]:
        chunks = []
        heading = section.get("heading", "")
        level = section.get("level", 1)
        content = section.get("content", "")

        if not content.strip():
            return chunks

        words = content.split()
        total_words = len(words)

        if total_words <= self.chunk_size:
            chunk_content = content
            if self.preserve_headings and level > 0 and heading:
                prefix = "#" * level + " " + heading + "\n\n"
                chunk_content = prefix + content

            chunks.append(ChunkResult(
                content=chunk_content,
                content_type=self._detect_content_type(content),
                metadata={
                    "section_index": section_index,
                    "heading": heading,
                    "heading_level": level,
                    "section_start_line": section.get("start_line", 0),
                    "section_end_line": section.get("end_line", 0),
                    "chunk_type": "section",
                    "word_count": len(chunk_content.split())
                }
            ))
        else:
            section_chunks = self._split_large_section(content, heading, level, section_index)
            chunks.extend(section_chunks)

        return chunks

    def _split_large_section(self, content: str, heading: str, level: int, section_index: int) -> List[ChunkResult]:
        chunks = []
        paragraphs = self._split_into_paragraphs(content)

        current_chunk_paragraphs = []
        current_word_count = 0

        for para in paragraphs:
            para_words = para.split()
            para_len = len(para_words)

            if not para_words:
                continue

            if para_len > self.chunk_size:
                if current_chunk_paragraphs:
                    chunk_text = '\n\n'.join(current_chunk_paragraphs)
                    chunks.append(self._create_section_chunk(chunk_text, heading, level, section_index))
                    current_chunk_paragraphs = []
                    current_word_count = 0

                chunks.extend(self._split_paragraph_into_chunks(para, heading, level, section_index))

            elif current_word_count + para_len > self.chunk_size:
                chunk_text = '\n\n'.join(current_chunk_paragraphs)
                chunks.append(self._create_section_chunk(chunk_text, heading, level, section_index))

                current_chunk_paragraphs = [para]
                current_word_count = para_len
            else:
                current_chunk_paragraphs.append(para)
                current_word_count += para_len

        if current_chunk_paragraphs:
            chunk_text = '\n\n'.join(current_chunk_paragraphs)
            chunks.append(self._create_section_chunk(chunk_text, heading, level, section_index))

        return chunks

    def _split_paragraph_into_chunks(self, para: str, heading: str, level: int, section_index: int) -> List[ChunkResult]:
        chunks = []
        words = para.split()
        sentences = self._split_into_sentences(para)

        if len(sentences) > 1 and len(sentences) <= 10:
            current_sentences = []
            current_word_count = 0

            for sentence in sentences:
                sentence_words = len(sentence.split())

                if current_word_count + sentence_words > self.chunk_size:
                    if current_sentences:
                        chunk_text = ' '.join(current_sentences)
                        chunks.append(self._create_section_chunk(chunk_text, heading, level, section_index, is_split=True))
                        current_sentences = []
                        current_word_count = 0

                current_sentences.append(sentence)
                current_word_count += sentence_words

            if current_sentences:
                chunk_text = ' '.join(current_sentences)
                chunks.append(self._create_section_chunk(chunk_text, heading, level, section_index, is_split=True))
        else:
            for i in range(0, len(words), self.chunk_size):
                chunk_words = words[i:i + self.chunk_size]
                chunk_text = ' '.join(chunk_words)
                chunks.append(self._create_section_chunk(chunk_text, heading, level, section_index, is_split=True))

        return chunks

    def _split_into_sentences(self, text: str) -> List[str]:
        sentence_pattern = r'(?<=[.!?。！？])\s+'
        sentences = re.split(sentence_pattern, text)
        return [s.strip() for s in sentences if s.strip()]

    def _create_section_chunk(self, content: str, heading: str, level: int, section_index: int, is_split: bool = False) -> ChunkResult:
        prefix = ""
        if self.preserve_headings and heading:
            prefix = "#" * level + " " + heading + "\n\n"

        chunk_content = prefix + content if prefix else content

        return ChunkResult(
            content=chunk_content,
            content_type=self._detect_content_type(content),
            metadata={
                "section_index": section_index,
                "heading": heading,
                "heading_level": level,
                "chunk_type": "section_split" if is_split else "section",
                "is_split": is_split,
                "word_count": len(chunk_content.split())
            }
        )

    def _chunk_by_paragraph(self, content: Dict[str, Any]) -> List[ChunkResult]:
        chunks = []
        blocks = content.get("blocks", [])

        current_chunk = []
        current_word_count = 0

        for block in blocks:
            block_type = block.get("type", "paragraph")
            block_content = block.get("content", "")
            block_words = block_content.split()
            block_word_count = len(block_words)

            if block_word_count == 0:
                continue

            if block_type in ["code_block", "table"]:
                if current_chunk:
                    chunk_result = self._create_chunk_from_blocks(current_chunk)
                    chunks.append(chunk_result)
                    current_chunk = []
                    current_word_count = 0

                chunks.append(ChunkResult(
                    content=block_content,
                    content_type="code" if block_type == "code_block" else "table",
                    metadata={
                        "block_type": block_type,
                        "language": block.get("metadata", {}).get("language", ""),
                        "chunk_type": "standalone_block"
                    }
                ))

            elif current_word_count + block_word_count > self.chunk_size:
                if current_chunk:
                    chunk_result = self._create_chunk_from_blocks(current_chunk)
                    chunks.append(chunk_result)

                    if self.overlap > 0 and len(current_chunk) > 0:
                        last_block = current_chunk[-1]
                        last_words = last_block.get("content", "").split()
                        overlap_size = min(self.overlap, len(last_words))
                        if overlap_size > 0:
                            overlap_text = ' '.join(last_words[-overlap_size:])
                            current_chunk = [{"content": overlap_text, "type": current_chunk[-1].get("type", "paragraph")}]
                            current_word_count = overlap_size
                        else:
                            current_chunk = []
                            current_word_count = 0
                    else:
                        current_chunk = []
                        current_word_count = 0

                if block_word_count > self.chunk_size:
                    chunks.extend(self._split_large_block(block))
                else:
                    current_chunk.append(block)
                    current_word_count = block_word_count

            else:
                current_chunk.append(block)
                current_word_count += block_word_count

        if current_chunk:
            chunk_result = self._create_chunk_from_blocks(current_chunk)
            chunks.append(chunk_result)

        return chunks

    def _split_into_paragraphs(self, content: str) -> List[str]:
        paragraphs = []
        current_lines = []

        for line in content.split('\n'):
            stripped = line.strip()

            if not stripped:
                if current_lines:
                    paragraphs.append('\n'.join(current_lines))
                    current_lines = []
            else:
                current_lines.append(line)

        if current_lines:
            paragraphs.append('\n'.join(current_lines))

        return [p for p in paragraphs if p.strip()]

    def _create_chunk_from_blocks(self, blocks: List[Dict]) -> ChunkResult:
        if not blocks:
            return ChunkResult(content="", content_type="text", metadata={})

        first_block = blocks[0]
        last_block = blocks[-1]

        heading = ""
        heading_level = 0
        for block in blocks:
            content = block.get("content", "").strip()
            if content.startswith('#'):
                match = re.match(r'^(#{1,6})\s+(.+)', content)
                if match:
                    heading = match.group(2)
                    heading_level = len(match.group(1))
                    break

        combined_content = '\n\n'.join(b.get("content", "") for b in blocks)

        primary_type = "text"
        for block in blocks:
            block_type = block.get("type", "text")
            if block_type in ["code", "table"]:
                primary_type = block_type
                break

        return ChunkResult(
            content=combined_content,
            content_type=primary_type,
            metadata={
                "first_block_line": first_block.get("metadata", {}).get("line", 0),
                "last_block_line": last_block.get("metadata", {}).get("line", 0),
                "block_count": len(blocks),
                "heading": heading,
                "heading_level": heading_level,
                "chunk_type": "merged_paragraphs",
                "word_count": len(combined_content.split())
            }
        )

    def _split_large_block(self, block: Dict[str, Any]) -> List[ChunkResult]:
        content = block.get("content", "")
        block_type = block.get("type", "paragraph")

        words = content.split()
        chunks = []

        for i in range(0, len(words), self.chunk_size):
            chunk_words = words[i:i + self.chunk_size]
            chunks.append(ChunkResult(
                content=' '.join(chunk_words),
                content_type=self._detect_content_type(' '.join(chunk_words)),
                metadata={
                    "original_block_type": block_type,
                    "chunk_index": len(chunks),
                    "is_split": True,
                    "chunk_type": "block_split"
                }
            ))

        return chunks

    def _detect_content_type(self, content: str) -> str:
        if content.startswith('```') or content.strip().endswith('```'):
            return "code"
        if '|' in content and content.count('|') >= 2:
            return "table"
        if re.match(r'^#{1,6}\s+', content.strip()):
            return "heading"
        if content.startswith('>'):
            return "quote"
        return "text"

    def _ensure_chunk_quality(self, chunks: List[ChunkResult]) -> List[ChunkResult]:
        quality_chunks = []

        for chunk in chunks:
            word_count = len(chunk.content.split())

            if word_count < self.min_chunk_size and len(quality_chunks) > 0:
                previous_chunk = quality_chunks[-1]
                combined_content = previous_chunk.content + "\n\n" + chunk.content
                combined_word_count = len(combined_content.split())

                if combined_word_count <= self.chunk_size:
                    quality_chunks[-1] = ChunkResult(
                        content=combined_content,
                        content_type=chunk.content_type,
                        metadata={
                            **previous_chunk.metadata,
                            "merged_with": chunk.metadata,
                            "chunk_type": "merged"
                        }
                    )
                    continue

            quality_chunks.append(chunk)

        return quality_chunks

    def _fallback_chunk(self, content: Dict[str, Any]) -> List[ChunkResult]:
        pages = content.get("pages", [])
        chunks = []

        for page in pages:
            text = page.get("text", "")
            page_num = page.get("page_num", 1)

            words = text.split()
            for i in range(0, len(words), self.chunk_size):
                chunk_words = words[i:i + self.chunk_size]
                chunks.append(ChunkResult(
                    content=' '.join(chunk_words),
                    content_type="text",
                    metadata={
                        "page_num": page_num,
                        "chunk_type": "fallback"
                    }
                ))

        return chunks
