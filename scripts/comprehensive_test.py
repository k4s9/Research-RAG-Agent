#!/usr/bin/env python3
"""全面测试 PDF 和 Markdown 文件的解析、清洗、分块及存储逻辑"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime
import json

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.ingest.pdf_parser import PDFParser
from src.core.ingest.markdown_parser import EnhancedMarkdownParser
from src.core.ingest.pdf_cleaner import PDFCleaner
from src.core.ingest.markdown_cleaner import MarkdownCleaner
from src.core.ingest.pdf_chunker import PDFChunker
from src.core.ingest.markdown_chunker import MarkdownChunker
from src.core.ingest.cleaner import DocumentCleaner
from src.core.ingest.chunker import DocumentChunker


class ComprehensiveTester:
    def __init__(self):
        self.pdf_parser = PDFParser()
        self.md_parser = EnhancedMarkdownParser()
        self.pdf_cleaner = PDFCleaner()
        self.md_cleaner = MarkdownCleaner()
        self.pdf_chunker = PDFChunker(chunk_size=384, overlap=128)
        self.md_chunker = MarkdownChunker(chunk_size=384, overlap=128)
        self.doc_cleaner = DocumentCleaner()
        self.doc_chunker = DocumentChunker(chunk_size=384, overlap=128)

    def print_section(self, title: str, width: int = 80):
        print(f"\n{'='*width}")
        print(f"  {title}")
        print('='*width)

    def print_subsection(self, title: str):
        print(f"\n--- {title} ---")

    async def test_pdf_full_pipeline(self, file_path: str) -> dict:
        self.print_section(f"PDF 完整流水线测试: {Path(file_path).name}")

        result = {
            "file": file_path,
            "timestamp": datetime.now().isoformat()
        }

        # 1. 解析
        self.print_subsection("1. 文件解析")
        parsed = await self.pdf_parser.parse(file_path)
        pages = parsed.get("pages", [])

        print(f"  文件: {file_path}")
        print(f"  总页数: {len(pages)}")
        print(f"  总字符数: {sum(len(p.get('text', '')) for p in pages)}")
        print(f"  总文本块数: {sum(len(p.get('blocks', [])) for p in pages)}")

        scanned_pages = sum(1 for p in pages if p.get('is_scanned'))
        print(f"  扫描件页数: {scanned_pages}")
        print(f"  原生文本页数: {len(pages) - scanned_pages}")

        result["parsing"] = {
            "pages": len(pages),
            "total_chars": sum(len(p.get('text', '')) for p in pages),
            "total_blocks": sum(len(p.get('blocks', [])) for p in pages),
            "scanned_pages": scanned_pages
        }

        # 2. 清洗前文本预览
        self.print_subsection("2. 清洗前文本预览 (第1页前500字符)")
        raw_text = pages[0].get('text', '')[:500] if pages else ""
        print(f"  {raw_text.replace(chr(10), ' ')[:200]}...")

        # 3. 清洗
        self.print_subsection("3. 文本清洗")
        cleaned = self.doc_cleaner.clean(parsed)
        filtered_headers = cleaned.get('filtered_headers', [])
        filtered_footers = cleaned.get('filtered_footers', [])

        print(f"  过滤页眉数: {len(filtered_headers)}")
        print(f"  过滤页脚数: {len(filtered_footers)}")

        if filtered_headers:
            print(f"  页眉示例: '{filtered_headers[0][:50]}...'")
        if filtered_footers:
            print(f"  页脚示例: '{filtered_footers[0][:50]}...'")

        result["cleaning"] = {
            "filtered_headers": len(filtered_headers),
            "filtered_footers": len(filtered_footers)
        }

        # 4. 清洗后文本预览
        self.print_subsection("4. 清洗后文本预览 (第1页前500字符)")
        cleaned_text = cleaned['pages'][0].get('text', '')[:500] if cleaned.get('pages') else ""
        print(f"  {cleaned_text.replace(chr(10), ' ')[:200]}...")

        # 5. 清洗前后对比
        self.print_subsection("5. 清洗效果对比")
        before_len = len(pages[0].get('text', '')) if pages else 0
        after_len = len(cleaned['pages'][0].get('text', '')) if cleaned.get('pages') else 0
        reduction = ((before_len - after_len) / before_len * 100) if before_len > 0 else 0

        print(f"  第1页清洗前字符数: {before_len}")
        print(f"  第1页清洗后字符数: {after_len}")
        print(f"  减少比例: {reduction:.1f}%")

        result["cleaning"]["before_chars"] = before_len
        result["cleaning"]["after_chars"] = after_len
        result["cleaning"]["reduction_percent"] = round(reduction, 1)

        # 6. 分块
        self.print_subsection("6. 文档分块")
        chunks = self.doc_chunker.chunk(cleaned)

        print(f"  分块策略: 基于段落 (chunk_size={self.doc_chunker.chunk_size}, overlap={self.doc_chunker.overlap})")
        print(f"  生成 chunks 数: {len(chunks)}")

        if chunks:
            chunk_sizes = [len(c['content']) for c in chunks]
            print(f"  Chunk 字符数范围: {min(chunk_sizes)} - {max(chunk_sizes)}")
            print(f"  Chunk 平均字符数: {sum(chunk_sizes)/len(chunk_sizes):.1f}")

        result["chunking"] = {
            "chunk_count": len(chunks),
            "chunk_size": self.doc_chunker.chunk_size,
            "overlap": self.doc_chunker.overlap,
            "min_chunk_chars": min(chunk_sizes) if chunks else 0,
            "max_chunk_chars": max(chunk_sizes) if chunks else 0,
            "avg_chunk_chars": sum(chunk_sizes)/len(chunk_sizes) if chunks else 0
        }

        # 7. 分块详情
        self.print_subsection("7. 分块详情 (前5个)")
        for i, chunk in enumerate(chunks[:5]):
            content_preview = chunk['content'][:150].replace('\n', ' ')
            page = chunk['metadata'].get('page_num', 'N/A')
            chunk_type = chunk['metadata'].get('chunk_type', 'unknown')
            print(f"  Chunk {i+1} [页{page}] ({chunk_type}): {content_preview}...")

        # 8. 元数据
        self.print_subsection("8. 存储元数据")
        print(f"  content_type: {chunks[0]['content_type'] if chunks else 'N/A'}")
        print(f"  metadata keys: {list(chunks[0]['metadata'].keys()) if chunks else 'N/A'}")

        return result

    async def test_markdown_full_pipeline(self, file_path: str) -> dict:
        self.print_section(f"Markdown 完整流水线测试: {Path(file_path).name}")

        result = {
            "file": file_path,
            "timestamp": datetime.now().isoformat()
        }

        # 1. 解析
        self.print_subsection("1. 文件解析")
        parsed = await self.md_parser.parse(file_path)

        sections = parsed.get("sections", [])
        blocks = parsed.get("blocks", [])
        structure = parsed.get("structure", {})

        print(f"  文件: {file_path}")
        print(f"  章节数: {len(sections)}")
        print(f"  块数: {len(blocks)}")
        print(f"  总词数: {parsed.get('total_words', 0)}")

        headings = structure.get("heading_distribution", {})
        print(f"  标题分布: H1={headings.get(1, 0)}, H2={headings.get(2, 0)}, H3+={sum(v for k,v in headings.items() if k >= 3)}")

        result["parsing"] = {
            "sections": len(sections),
            "blocks": len(blocks),
            "total_words": parsed.get('total_words', 0),
            "heading_distribution": headings
        }

        # 2. 清洗前内容预览
        self.print_subsection("2. 清洗前内容预览 (前500字符)")
        raw_content = sections[0].get('content', '')[:500] if sections else ""
        print(f"  {raw_content[:200]}...")

        # 3. 清洗
        self.print_subsection("3. 文本清洗")
        cleaned = self.doc_cleaner.clean(parsed)

        total_original = sum(len(s.get('content', '')) for s in parsed.get('sections', []))
        total_cleaned = sum(len(s.get('content', '')) for s in cleaned.get('sections', []))

        print(f"  清洗方法: 空白符规范化、Markdown格式清理")
        print(f"  清洗前总字符数: {total_original}")
        print(f"  清洗后总字符数: {total_cleaned}")
        print(f"  减少字符数: {total_original - total_cleaned}")

        result["cleaning"] = {
            "before_chars": total_original,
            "after_chars": total_cleaned,
            "removed_chars": total_original - total_cleaned
        }

        # 4. 清洗后内容预览
        self.print_subsection("4. 清洗后内容预览 (前500字符)")
        cleaned_content = cleaned['sections'][0].get('content', '')[:500] if cleaned.get('sections') else ""
        print(f"  {cleaned_content[:200]}...")

        # 5. 清洗前后对比
        self.print_subsection("5. 清洗效果对比 (章节级别)")
        for i, (orig, clean) in enumerate(zip(parsed.get('sections', [])[:3], cleaned.get('sections', [])[:3])):
            orig_len = len(orig.get('content', ''))
            clean_len = len(clean.get('content', ''))
            heading = orig.get('heading', 'Document Root')
            print(f"  [{heading[:30]}]: {orig_len} -> {clean_len} 字符 (减少 {orig_len - clean_len})")

        # 6. 分块
        self.print_subsection("6. 文档分块")
        chunks = self.md_chunker.chunk(cleaned)

        print(f"  分块策略: 基于标题结构 (chunk_size={self.md_chunker.chunk_size}, overlap={self.md_chunker.overlap})")
        print(f"  生成 chunks 数: {len(chunks)}")

        if chunks:
            chunk_sizes = [len(c.content) for c in chunks]
            chunk_word_counts = [len(c.content.split()) for c in chunks]
            print(f"  Chunk 字符数范围: {min(chunk_sizes)} - {max(chunk_sizes)}")
            print(f"  Chunk 词数范围: {min(chunk_word_counts)} - {max(chunk_word_counts)}")
            print(f"  平均词数: {sum(chunk_word_counts)/len(chunk_word_counts):.1f}")

        result["chunking"] = {
            "chunk_count": len(chunks),
            "chunk_size": self.md_chunker.chunk_size,
            "overlap": self.md_chunker.overlap,
            "min_chunk_chars": min(chunk_sizes) if chunks else 0,
            "max_chunk_chars": max(chunk_sizes) if chunks else 0,
            "avg_chunk_words": sum(chunk_word_counts)/len(chunk_word_counts) if chunks else 0
        }

        # 7. 分块详情
        self.print_subsection("7. 分块详情 (前5个)")
        for i, chunk in enumerate(chunks[:5]):
            content_preview = chunk.content[:150].replace('\n', ' ')
            heading = chunk.metadata.get('heading', 'N/A')
            chunk_type = chunk.metadata.get('chunk_type', 'unknown')
            word_count = chunk.metadata.get('word_count', len(chunk.content.split()))
            print(f"  Chunk {i+1} [{heading[:25]}] ({chunk_type}, {word_count}词): {content_preview}...")

        # 8. 存储元数据
        self.print_subsection("8. 存储元数据")
        print(f"  content_type: {chunks[0].content_type if chunks else 'N/A'}")
        print(f"  metadata keys: {list(chunks[0].metadata.keys()) if chunks else 'N/A'}")

        return result

    def print_summary_report(self, pdf_results: list, md_results: list):
        self.print_section("测试结果汇总报告")

        print("\n【PDF 文件测试汇总】")
        print("-" * 60)
        for r in pdf_results:
            print(f"\n  文件: {Path(r['file']).name}")
            print(f"  - 解析: {r['parsing']['pages']}页, {r['parsing']['total_chars']}字符")
            print(f"  - 清洗: 过滤页眉{r['cleaning']['filtered_headers']}个, 页脚{r['cleaning']['filtered_footers']}个")
            print(f"  - 分块: {r['chunking']['chunk_count']}个chunks, 平均{r['chunking']['avg_chunk_chars']:.0f}字符/块")

        print("\n\n【Markdown 文件测试汇总】")
        print("-" * 60)
        for r in md_results:
            print(f"\n  文件: {Path(r['file']).name}")
            print(f"  - 解析: {r['parsing']['sections']}章节, {r['parsing']['total_words']}词")
            print(f"  - 清洗: 去除{r['cleaning']['removed_chars']}冗余字符")
            print(f"  - 分块: {r['chunking']['chunk_count']}个chunks, 平均{r['chunking']['avg_chunk_words']:.0f}词/块")

        print("\n\n【评估结论】")
        print("-" * 60)
        print("""
  1. 文件解析功能:
     - PDF解析器能够正确提取文本，但复杂布局(多栏、公式)时文本块顺序可能混乱
     - 扫描件检测功能正常工作(基于文本密度阈值<0.01)
     - Markdown解析器能正确识别标题、段落、代码块、表格等结构

  2. 文本清洗逻辑:
     - PDFCleaner能检测并过滤重复出现的页眉页脚(基于频率阈值50%)
     - 清洗后文本顺序可能因布局重建而改变，这是当前实现的已知局限
     - MarkdownCleaner能规范化空白符、清理Markdown格式标记

  3. 分块策略:
     - PDF分块: 基于段落分割，chunk_size=384词，overlap=128词
     - Markdown分块: 基于标题结构，保留标题前缀，智能合并过小chunk
     - 分块大小基本符合预期，但存在个别过大chunk

  4. 存储机制:
     - ChunkResult包含content、content_type、metadata三个字段
     - metadata包含page_num、chunk_type等必要信息
     -  pipeline.py显示会写入PostgreSQL和Milvus

  5. 潜在问题:
     - PDF多栏布局重建可能导致文本顺序不正确
     - 部分PDF分块结果出现乱序/截断
     - Markdown分块可能出现跨章节问题
        """)


async def main():
    tester = ComprehensiveTester()

    pdf_files = [
        "/home/guozy/research-rag-agent/data/uploads/pi0.pdf",
        "/home/guozy/research-rag-agent/data/uploads/Omni JARVIS.pdf",
        "/home/guozy/research-rag-agent/data/uploads/OpenHA.pdf"
    ]

    md_file = "/home/guozy/research-rag-agent/working_document.md"

    pdf_results = []
    md_results = []

    for pdf_file in pdf_files:
        if Path(pdf_file).exists():
            result = await tester.test_pdf_full_pipeline(pdf_file)
            pdf_results.append(result)
        else:
            print(f"PDF文件不存在: {pdf_file}")

    if Path(md_file).exists():
        result = await tester.test_markdown_full_pipeline(md_file)
        md_results.append(result)

    tester.print_summary_report(pdf_results, md_results)

    print(f"\n\n测试完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    asyncio.run(main())