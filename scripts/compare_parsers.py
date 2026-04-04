#!/usr/bin/env python3
"""PDF 解析器对比测试脚本

对比原始解析器和增强版解析器的效果，验证多栏布局处理能力。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.ingest.pdf_parser import PDFParser
from src.core.ingest.pdf_parser_enhanced import EnhancedPDFParser


async def compare_parsers(file_path: str):
    print("="*70)
    print("  PDF 解析器对比测试")
    print("="*70)
    print(f"\n文件: {file_path}\n")

    original_parser = PDFParser()
    enhanced_parser = EnhancedPDFParser()

    print("-"*70)
    print("  1. 原始解析器 (PDFParser)")
    print("-"*70)

    try:
        original_result = await original_parser.parse(file_path)
        original_pages = original_result.get("pages", [])

        print(f"\n总页数: {len(original_pages)}")

        for i, page in enumerate(original_pages[1:3]):
            print(f"\n第 {i+2} 页原始解析结果 (前500字符):")
            text = page["text"][:500].replace('\n', ' ')
            print(f"  {text}...")

            words = page["text"].split()
            word_lengths = [len(w) for w in words if len(w) > 1]
            avg_len = sum(word_lengths) / len(word_lengths) if word_lengths else 0
            double_spaces = page["text"].count('  ')

            print(f"\n  词数统计: {len(words)} 词")
            print(f"  平均词长: {avg_len:.2f}")
            print(f"  连续空格数: {double_spaces}")

            if double_spaces > 5 or avg_len < 4:
                print(f"  [!] 警告: 检测到可能的布局问题")

    except Exception as e:
        print(f"解析失败: {e}")

    print("\n" + "-"*70)
    print("  2. 增强解析器 (EnhancedPDFParser)")
    print("-"*70)

    try:
        enhanced_result = await enhanced_parser.parse(file_path)
        enhanced_pages = enhanced_result.get("pages", [])

        print(f"\n总页数: {len(enhanced_pages)}")

        for i, page in enumerate(enhanced_pages[1:3]):
            print(f"\n第 {i+2} 页增强解析结果 (前500字符):")
            text = page["text"][:500].replace('\n', ' ')
            print(f"  {text}...")

            words = page["text"].split()
            word_lengths = [len(w) for w in words if len(w) > 1]
            avg_len = sum(word_lengths) / len(word_lengths) if word_lengths else 0
            double_spaces = page["text"].count('  ')

            print(f"\n  词数统计: {len(words)} 词")
            print(f"  平均词长: {avg_len:.2f}")
            print(f"  连续空格数: {double_spaces}")

    except Exception as e:
        print(f"解析失败: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*70)
    print("  对比总结")
    print("="*70)
    print("""
检查指标:
  1. 词间空格数量 - 过多空格表明布局处理有问题
  2. 平均词长 - 英文正常约 5-6，中文约 1-2
  3. 语义完整性 - 句子是否被正确切分
  4. 多栏处理 - 多栏文档是否被正确合并/分离
""")


async def main():
    test_file = "/home/guozy/research-rag-agent/data/uploads/Omni JARVIS.pdf"

    if len(sys.argv) > 1:
        test_file = sys.argv[1]

    if not Path(test_file).exists():
        print(f"[!] 文件不存在: {test_file}")
        sys.exit(1)

    await compare_parsers(test_file)


if __name__ == "__main__":
    asyncio.run(main())
