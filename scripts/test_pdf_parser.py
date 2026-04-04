#!/usr/bin/env python3
"""PDF 解析测试脚本

用于测试 PDF 解析功能，诊断解析质量问题。
支持多种布局和复杂度的 PDF 文件测试。
"""

import asyncio
import argparse
import sys
from pathlib import Path
from datetime import datetime
import json

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.ingest.pdf_parser import PDFParser
from src.core.ingest.cleaner import DocumentCleaner
from src.core.ingest.chunker import DocumentChunker


class PDFParserTester:
    def __init__(self):
        self.parser = PDFParser()
        self.cleaner = DocumentCleaner()
        self.chunker = DocumentChunker(chunk_size=384, overlap=128)

    def print_header(self, title: str):
        print(f"\n{'='*70}")
        print(f"  {title}")
        print('='*70)

    def print_page_summary(self, pages: list):
        print(f"\n总页数: {len(pages)}")

        for i, page in enumerate(pages[:3]):
            print(f"\n--- 第 {page['page_num']} 页预览 ---")
            text = page['text']
            print(f"文本长度: {len(text)} 字符")
            print(f"文本块数: {len(page.get('blocks', []))}")
            print(f"是否为扫描件: {page.get('is_scanned', False)}")

            preview = text[:500].replace('\n', ' ')
            if len(text) > 500:
                preview += "..."
            print(f"内容预览:\n{preview}")

    def analyze_text_quality(self, pages: list):
        self.print_header("文本质量分析")

        total_chars = 0
        total_words = 0
        excessive_space_count = 0
        line_break_issues = 0

        for page in pages:
            text = page['text']
            total_chars += len(text)

            words = text.split()
            total_words += len(words)

            excessive_space_count += len([w for w in text.split() if len(w) > 1 and '  ' in w])

            lines = text.split('\n')
            for line in lines:
                if line.strip() != line and len(line.strip()) > 0:
                    line_break_issues += 1

        avg_word_length = total_chars / max(total_words, 1)

        print(f"\n总体统计:")
        print(f"  - 总字符数: {total_chars}")
        print(f"  - 总词数: {total_words}")
        print(f"  - 平均词长: {avg_word_length:.2f}")

        print(f"\n质量问题检测:")
        print(f"  - 连续空格出现次数: {excessive_space_count}")
        print(f"  - 换行符异常: {line_break_issues}")

        if excessive_space_count > 10 or avg_word_length < 3:
            print(f"\n[!] 检测到文本质量问题: 词间空格过多")
            print(f"    可能原因: PDF布局复杂(多栏、表格)导致文本提取时位置重叠")
            return False
        return True

    def analyze_block_structure(self, pages: list):
        self.print_header("文本块结构分析")

        for page in pages[:2]:
            blocks = page.get('blocks', [])
            if not blocks:
                print(f"\n第 {page['page_num']} 页: 无文本块信息")
                continue

            print(f"\n第 {page['page_num']} 页:")
            print(f"  文本块数量: {len(blocks)}")

            positions = []
            for block in blocks[:10]:
                bbox = block.get('bbox', [])
                text = block.get('text', '')[:30]
                font_size = block.get('font_size', 0)
                positions.append((bbox[1] if bbox else 0, text, font_size))

            positions.sort()
            print(f"  前10个文本块(按Y坐标排序):")
            for y_pos, text, font_size in positions[:10]:
                print(f"    Y={y_pos:.1f}: \"{text}\" (字体大小: {font_size:.1f})")

    async def test_parsing(self, file_path: str):
        self.print_header(f"PDF 解析测试: {Path(file_path).name}")

        if not Path(file_path).exists():
            print(f"[!] 文件不存在: {file_path}")
            return None

        try:
            result = await self.parser.parse(file_path)
            pages = result.get('pages', [])

            print(f"\n[✓] PDF 解析成功")
            self.print_page_summary(pages)

            has_issues = not self.analyze_text_quality(pages)
            self.analyze_block_structure(pages)

            return {
                "success": True,
                "pages": pages,
                "has_issues": has_issues
            }

        except Exception as e:
            print(f"\n[✗] PDF 解析失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def test_cleaning(self, parsed_content: dict):
        self.print_header("文档清洗测试")

        try:
            cleaned = self.cleaner.clean(parsed_content)

            filtered_headers = cleaned.get('filtered_headers', [])
            filtered_footers = cleaned.get('filtered_footers', [])

            print(f"\n[✓] 文档清洗完成")
            print(f"  - 过滤页眉数: {len(filtered_headers)}")
            print(f"  - 过滤页脚数: {len(filtered_footers)}")

            if filtered_headers:
                print(f"  - 页眉内容示例: {filtered_headers[0][:50]}...")
            if filtered_footers:
                print(f"  - 页脚内容示例: {filtered_footers[0][:50]}...")

            for i, page in enumerate(cleaned.get('pages', [])[:2]):
                original_text = parsed_content['pages'][i]['text'][:200]
                cleaned_text = page['text'][:200]
                print(f"\n第 {i+1} 页清洗效果对比:")
                print(f"  原始: {original_text[:100]}...")
                print(f"  清洗后: {cleaned_text[:100]}...")

            return cleaned

        except Exception as e:
            print(f"[!] 文档清洗失败: {e}")
            return parsed_content

    def test_chunker(self, cleaned_content: dict):
        self.print_header("文档分块测试")

        try:
            chunks = self.chunker.chunk(cleaned_content)

            print(f"\n[✓] 文档分块完成")
            print(f"  - 生成 chunk 数: {len(chunks)}")
            print(f"  - chunk_size: {self.chunker.chunk_size}")
            print(f"  - overlap: {self.chunker.overlap}")

            chunk_sizes = [len(c['content']) for c in chunks]
            print(f"\n  - chunk 字符数统计:")
            print(f"    最小: {min(chunk_sizes) if chunk_sizes else 0}")
            print(f"    最大: {max(chunk_sizes) if chunk_sizes else 0}")
            print(f"    平均: {sum(chunk_sizes) / len(chunk_sizes) if chunk_sizes else 0:.1f}")

            print(f"\n前 3 个 chunk 内容预览:")
            for i, chunk in enumerate(chunks[:3]):
                content = chunk['content'][:300].replace('\n', ' ')
                print(f"\n  Chunk {i+1} (page {chunk['metadata'].get('page_num', 'N/A')}):")
                print(f"    {content}...")

            return chunks

        except Exception as e:
            print(f"[!] 文档分块失败: {e}")
            import traceback
            traceback.print_exc()
            return []

    async def run_full_test(self, file_path: str):
        print("\n" + "="*70)
        print("  PDF 解析与分块完整测试")
        print(f"  文件: {file_path}")
        print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70)

        parsed = await self.test_parsing(file_path)
        if not parsed:
            return

        if parsed.get('has_issues'):
            print("\n[!] 警告: PDF 解析存在质量问题，请检查上述分析")

        cleaned = self.test_cleaning(parsed)
        chunks = self.test_chunker(cleaned)

        self.print_header("测试总结")
        print(f"  - 文件: {file_path}")
        print(f"  - 页数: {len(parsed['pages'])}")
        print(f"  - 生成 chunks: {len(chunks)}")
        print(f"  - 解析质量: {'[有警告]' if parsed.get('has_issues') else '[正常]'}")

        return {
            "parsed": parsed,
            "cleaned": cleaned,
            "chunks": chunks
        }


async def main():
    parser = argparse.ArgumentParser(description="PDF 解析测试工具")
    parser.add_argument("file", type=str, help="PDF 文件路径")
    parser.add_argument("--export", "-e", type=str, help="导出结果到 JSON 文件")

    args = parser.parse_args()

    tester = PDFParserTester()
    result = await tester.run_full_test(args.file)

    if result and args.export:
        export_path = Path(args.export)
        with open(export_path, 'w', encoding='utf-8') as f:
            json.dump({
                "file": args.file,
                "timestamp": datetime.now().isoformat(),
                "page_count": len(result["parsed"]["pages"]),
                "chunk_count": len(result["chunks"]),
                "has_parsing_issues": result["parsed"].get("has_issues", False)
            }, f, ensure_ascii=False, indent=2)
        print(f"\n[✓] 结果已导出到: {export_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python test_pdf_parser.py <pdf文件路径>")
        print("示例: python test_pdf_parser.py ./data/uploads/paper.pdf")
        sys.exit(1)

    asyncio.run(main())
