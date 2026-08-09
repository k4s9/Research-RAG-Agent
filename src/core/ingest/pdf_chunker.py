from typing import Dict, Any, List
from loguru import logger

from src.core.ingest.base import BaseChunker, ChunkResult


class PDFChunker(BaseChunker):
    def __init__(self, chunk_size: int = 384, overlap: int = 128, min_chunk_size: int = 50):
        super().__init__(chunk_size, overlap)
        self.min_chunk_size = min_chunk_size

    def chunk(self, cleaned_content: Dict[str, Any]) -> List[ChunkResult]:
        if cleaned_content.get("file_type") != "pdf":
            raise ValueError("PDFChunker 只接受 file_type=pdf 的内容")

        pages = cleaned_content.get("pages", [])
        chunks = []

        for i, page in enumerate(pages):
            page_chunks = self._chunk_page(page, i)
            chunks.extend(page_chunks)

        chunks = self._apply_overlap(chunks)

        logger.info(f"PDF 分块完成: 生成 {len(chunks)} 个 chunks")
        return chunks

    def _chunk_page(self, page: Dict[str, Any], page_index: int) -> List[ChunkResult]:
        text = page.get("text", "")
        page_num = page.get("page_num", page_index + 1)
        is_scanned = page.get("is_scanned", False)

        if is_scanned:
            return self._chunk_scanned_page(text, page_num)
        else:
            return self._chunk_text_page(text, page_num)

    def _chunk_text_page(self, text: str, page_num: int) -> List[ChunkResult]:
        paragraphs = text.split('\n\n')
        chunks = []
        current_paragraphs = []
        current_word_count = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_words = len(para.split())

            if para_words > self.chunk_size:
                if current_paragraphs:
                    chunk_result = self._create_chunk(current_paragraphs, page_num)
                    chunks.append(chunk_result)
                    current_paragraphs = []
                    current_word_count = 0

                sub_chunks = self._split_large_paragraph(para, page_num)
                chunks.extend(sub_chunks)

            elif current_word_count + para_words > self.chunk_size:
                chunk_result = self._create_chunk(current_paragraphs, page_num)
                chunks.append(chunk_result)

                current_paragraphs = [para]
                current_word_count = para_words
            else:
                current_paragraphs.append(para)
                current_word_count += para_words

        if current_paragraphs:
            chunk_result = self._create_chunk(current_paragraphs, page_num)
            chunks.append(chunk_result)

        return chunks

    def _chunk_scanned_page(self, text: str, page_num: int) -> List[ChunkResult]:
        words = text.split()
        chunks = []

        for i in range(0, len(words), self.chunk_size - self.overlap):
            chunk_words = words[i:i + self.chunk_size]
            chunk_text = ' '.join(chunk_words)

            chunks.append(ChunkResult(
                content=chunk_text,
                content_type="text",
                metadata={
                    "page_num": page_num,
                    "page_start": page_num,
                    "page_end": page_num,
                    "is_scanned": True,
                    "chunk_type": "scanned_split"
                }
            ))

        return chunks

    def _split_large_paragraph(self, para: str, page_num: int) -> List[ChunkResult]:
        sentences = self._split_into_sentences(para)
        chunks = []
        current_sentences = []
        current_word_count = 0

        for sentence in sentences:
            sentence_words = len(sentence.split())

            if current_word_count + sentence_words > self.chunk_size:
                if current_sentences:
                    chunk_text = ' '.join(current_sentences)
                    chunks.append(ChunkResult(
                        content=chunk_text,
                        content_type="text",
                        metadata={
                            "page_num": page_num,
                            "page_start": page_num,
                            "page_end": page_num,
                            "chunk_type": "paragraph_split"
                        }
                    ))
                    current_sentences = [sentence]
                    current_word_count = sentence_words
                else:
                    words = sentence.split()
                    for j in range(0, len(words), self.chunk_size):
                        sub_words = words[j:j + self.chunk_size]
                        chunks.append(ChunkResult(
                            content=' '.join(sub_words),
                            content_type="text",
                            metadata={
                                "page_num": page_num,
                                "page_start": page_num,
                                "page_end": page_num,
                                "chunk_type": "word_split"
                            }
                        ))
            else:
                current_sentences.append(sentence)
                current_word_count += sentence_words

        if current_sentences:
            chunk_text = ' '.join(current_sentences)
            chunks.append(ChunkResult(
                content=chunk_text,
                content_type="text",
                metadata={
                    "page_num": page_num,
                    "page_start": page_num,
                    "page_end": page_num,
                    "chunk_type": "paragraph_split"
                }
            ))

        return chunks

    def _split_into_sentences(self, text: str) -> List[str]:
        import re
        sentence_pattern = r'(?<=[.!?。！？])\s+'
        sentences = re.split(sentence_pattern, text)
        return [s.strip() for s in sentences if s.strip()]

    def _create_chunk(self, paragraphs: List[str], page_num: int) -> ChunkResult:
        combined_text = '\n\n'.join(paragraphs)
        word_count = len(combined_text.split())

        return ChunkResult(
            content=combined_text,
            content_type="text",
            metadata={
                "page_num": page_num,
                "page_start": page_num,
                "page_end": page_num,
                "paragraph_count": len(paragraphs),
                "word_count": word_count,
                "chunk_type": "paragraph_merge"
            }
        )

    def _apply_overlap(self, chunks: List[ChunkResult]) -> List[ChunkResult]:
        if self.overlap == 0 or len(chunks) < 2:
            return chunks

        overlapped_chunks = []
        previous_content = ""

        for i, chunk in enumerate(chunks):
            same_page = (
                i > 0
                and chunks[i - 1].metadata.get("page_num") == chunk.metadata.get("page_num")
            )
            if previous_content and same_page:
                words = previous_content.split()
                overlap_words = words[-min(self.overlap, len(words)):] if words else []
                overlap_text = ' '.join(overlap_words)

                combined_content = overlap_text + "\n\n" + chunk.content

                overlapped_chunks.append(ChunkResult(
                    content=combined_content,
                    content_type=chunk.content_type,
                    metadata={
                        **chunk.metadata,
                        "has_overlap": True,
                        "overlap_words": len(overlap_words)
                    }
                ))
            else:
                overlapped_chunks.append(chunk)

            previous_content = chunk.content

        return overlapped_chunks

    def _fallback_chunk(self, content: Dict[str, Any]) -> List[ChunkResult]:
        pages = content.get("pages", [])
        chunks = []

        for page in pages:
            text = page.get("text", "")
            page_num = page.get("page_num", 1)

            words = text.split()
            for i in range(0, len(words), self.chunk_size - self.overlap):
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
