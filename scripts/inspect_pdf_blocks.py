#!/usr/bin/env python3
"""检查 PDF 解析器原始 blocks 数据"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.ingest.pdf_parser import PDFParser


async def inspect_raw_blocks(file_path: str):
    print("="*70)
    print("  检查 PDF 原始 Blocks 数据")
    print("="*70)

    parser = PDFParser()
    result = await parser.parse(file_path)
    pages = result.get("pages", [])

    page2 = pages[1]

    print(f"\n第2页 blocks 信息:")
    print(f"  总 blocks 数: {len(page2.get('blocks', []))}")

    blocks = page2.get('blocks', [])

    print("\n前20个 blocks (按原始顺序):")
    for i, block in enumerate(blocks[:20]):
        bbox = block.get('bbox', [])
        text = block.get('text', '')[:40]
        y0, y1 = bbox[1], bbox[3]
        print(f"  {i}: Y={y0:.1f}-{y1:.1f} | \"{text}\"")

    print("\n\nBlocks 按 Y 坐标排序 (检查是否有交错):")
    sorted_by_y = sorted(blocks, key=lambda b: b.get('bbox', [0,0,0,0])[1])

    current_y_range = None
    for i, block in enumerate(sorted_by_y[:30]):
        bbox = block.get('bbox', [])
        text = block.get('text', '')[:30]
        y_center = (bbox[1] + bbox[3]) / 2

        if current_y_range is None:
            current_y_range = (bbox[1], bbox[3])
        elif bbox[1] > current_y_range[1]:
            print(f"\n--- 新的行 (Y={bbox[1]:.1f}) ---")
            current_y_range = (bbox[1], bbox[3])

        print(f"  X={bbox[0]:.1f} | \"{text}\"")


async def main():
    test_file = "/home/guozy/research-rag-agent/data/uploads/Omni JARVIS.pdf"
    if len(sys.argv) > 1:
        test_file = sys.argv[1]
    await inspect_raw_blocks(test_file)


if __name__ == "__main__":
    asyncio.run(main())
