#!/usr/bin/env python3
"""详细诊断多栏文本分离问题"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.ingest.pdf_parser import PDFParser
from src.core.ingest.cleaner import DocumentCleaner


async def diagnose():
    file_path = "/home/guozy/research-rag-agent/data/uploads/Omni JARVIS.pdf"

    parser = PDFParser()
    cleaner = DocumentCleaner()

    result = await parser.parse(file_path)
    pages = result.get("pages", [])
    page2 = pages[1]

    print("="*70)
    print("  多栏文本分离诊断")
    print("="*70)

    print(f"\n页面尺寸: {page2['width']} x {page2['height']}")
    mid_x = page2['width'] / 2
    print(f"中间分割线 X = {mid_x}")

    blocks = page2.get('blocks', [])

    print("\n按 span 分组的 blocks (共 {} 个):".format(len(blocks)))

    left_blocks = []
    right_blocks = []

    for i, block in enumerate(blocks[:50]):
        bbox = block.get('bbox', [])
        text = block.get('text', '')[:30].ljust(30)
        center_x = (bbox[0] + bbox[2]) / 2

        col = "LEFT" if center_x < mid_x else "RIGHT"

        if col == "LEFT":
            left_blocks.append(block)
        else:
            right_blocks.append(block)

        print(f"  {i}: X={center_x:6.1f} [{col}] \"{text}\"")

    print(f"\n左栏 blocks: {len(left_blocks)}")
    print(f"右栏 blocks: {len(right_blocks)}")

    print("\n左栏内容:")
    for b in left_blocks[:10]:
        print(f"  \"{b['text'][:50]}\"")

    print("\n右栏内容:")
    for b in right_blocks[:10]:
        print(f"  \"{b['text'][:50]}\"")

    print("\n清洗后重建结果:")
    cleaned = cleaner.clean(result)
    cleaned_page2 = cleaned['pages'][1]
    print(cleaned_page2['text'][:500])


if __name__ == "__main__":
    asyncio.run(diagnose())
