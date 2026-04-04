#!/usr/bin/env python3
"""调试分块问题"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.ingest.markdown_parser import EnhancedMarkdownParser
from src.core.ingest.markdown_cleaner import MarkdownCleaner
from src.core.ingest.markdown_chunker import MarkdownChunker


async def debug_chunks():
    file_path = "/home/guozy/research-rag-agent/working_document.md"

    parser = EnhancedMarkdownParser()
    cleaner = MarkdownCleaner()
    chunker = MarkdownChunker(chunk_size=384, overlap=128)

    result = await parser.parse(file_path)
    cleaned = cleaner.clean(result)
    chunks = chunker.chunk(cleaned)

    print(f"\n总共 {len(chunks)} 个 chunks\n")

    for i, chunk in enumerate(chunks):
        words = len(chunk.content.split())
        chars = len(chunk.content)
        chunk_type = chunk.metadata.get('chunk_type', 'unknown')
        heading = chunk.metadata.get('heading', 'N/A')
        is_split = chunk.metadata.get('is_split', False)

        status = "⚠ 过大" if words > 512 else "✓"
        print(f"Chunk {i+1:2d}: {words:4d} words, {chars:5d} chars [{status}] | {chunk_type:20s} | {heading[:40]}")

        if words > 512:
            print(f"       ^^^ 超过 512 words!")
            content_preview = chunk.content[:200].replace('\n', ' ')
            print(f"       内容预览: {content_preview}...")

    print("\n\n按 chunk_type 统计:")
    type_counts = {}
    for c in chunks:
        ct = c.metadata.get('chunk_type', 'unknown')
        type_counts[ct] = type_counts.get(ct, 0) + 1
    for ct, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {ct}: {count}")


if __name__ == "__main__":
    asyncio.run(debug_chunks())
