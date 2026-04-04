#!/usr/bin/env python3
"""PDF 解析全链路诊断脚本

追踪 PDF 解析 -> 清洗 -> 分块 -> 存储 的全过程，定位问题源头。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.ingest.pdf_parser import PDFParser
from src.core.ingest.cleaner import DocumentCleaner
from src.core.ingest.chunker import DocumentChunker


async def diagnose_pipeline(file_path: str):
    print("="*70)
    print("  PDF 解析全链路诊断")
    print("="*70)
    print(f"\n文件: {file_path}\n")

    parser = PDFParser()
    cleaner = DocumentCleaner()
    chunker = DocumentChunker(chunk_size=384, overlap=128)

    print("-"*70)
    print("  Step 1: PDF 解析")
    print("-"*70)

    parsed = await parser.parse(file_path)
    pages = parsed.get("pages", [])

    print(f"总页数: {len(pages)}")

    page2_text = pages[1]["text"]
    print(f"\n第2页解析结果 (原始):")
    print(f"  长度: {len(page2_text)} 字符")
    print(f"  前300字符: {page2_text[:300]}")
    print(f"  连续空格数: {page2_text.count('  ')}")

    print("\n" + "-"*70)
    print("  Step 2: 文档清洗")
    print("-"*70)

    cleaned = cleaner.clean(parsed)
    cleaned_pages = cleaned.get("pages", [])

    print(f"过滤的页眉: {cleaned.get('filtered_headers', [])[:3]}")
    print(f"过滤的页脚: {cleaned.get('filtered_footers', [])[:3]}")

    cleaned_page2_text = cleaned_pages[1]["text"]
    print(f"\n第2页清洗后:")
    print(f"  长度: {len(cleaned_page2_text)} 字符")
    print(f"  前300字符: {cleaned_page2_text[:300]}")
    print(f"  连续空格数: {cleaned_page2_text.count('  ')}")

    print("\n清洗前后对比:")
    print(f"  原始长度: {len(page2_text)}")
    print(f"  清洗后长度: {len(cleaned_page2_text)}")
    print(f"  变化: {len(cleaned_page2_text) - len(page2_text)}")

    if page2_text != cleaned_page2_text:
        print("\n[!] 清洗过程修改了文本内容!")

        i = 0
        while i < min(len(page2_text), len(cleaned_page2_text)):
            if page2_text[i] != cleaned_page2_text[i]:
                print(f"\n首次差异在位置 {i}:")
                print(f"  原始: '...{page2_text[max(0,i-20):i+50]}...'")
                print(f"  清洗后: '...{cleaned_page2_text[max(0,i-20):i+50]}...'")
                break
            i += 1

    print("\n" + "-"*70)
    print("  Step 3: 文档分块")
    print("-"*70)

    chunks = chunker.chunk(cleaned)

    print(f"生成了 {len(chunks)} 个 chunks")

    for i, chunk in enumerate(chunks[1:3]):
        chunk_text = chunk["content"]
        print(f"\nChunk {i+2}:")
        print(f"  长度: {len(chunk_text)} 字符")
        print(f"  前200字符: {chunk_text[:200]}")
        print(f"  连续空格数: {chunk_text.count('  ')}")

    print("\n" + "-"*70)
    print("  Step 4: 注入 Milvus 的原始数据")
    print("-"*70)

    for i, chunk in enumerate(chunks[1:3]):
        chunk_text = chunk["content"]

        print(f"\nChunk {i+2} 将存储到 Milvus 的 'content' 字段:")
        print(f"  字符数: {len(chunk_text)}")
        print(f"  连续空格数: {chunk_text.count('  ')}")

        words = chunk_text.split()
        word_lengths = [len(w) for w in words[:20]]
        print(f"  前20个词的词长: {word_lengths}")

        has_issue = any(w for w in words[:5] if len(w) > 15)
        if has_issue:
            print(f"  [!] 检测到超长词，可能存在问题")

    print("\n" + "="*70)
    print("  诊断结论")
    print("="*70)
    print("""
如果 Milvus 中存储的数据存在问题，请检查:

1. 【问题在解析阶段】
   - 症状: parsed_text 就有多余空格
   - 原因: PDF 是多栏布局或扫描件
   - 解决: 使用 EnhancedPDFParser 或启用 OCR

2. 【问题在清洗阶段】
   - 症状: cleaned_text 与 parsed_text 不同
   - 原因: DocumentCleaner 的页眉页脚过滤逻辑有问题
   - 解决: 检查 cleaner.py 的过滤规则

3. 【问题在分块阶段】
   - 症状: chunk.content 与 cleaned_text 不同
   - 原因: chunker 的文本处理导致
   - 解决: 检查 chunker.py 的 chunk() 方法

4. 【问题在存储阶段】
   - 症状: 数据库内容与 chunk.content 不同
   - 原因: embedder 或 Milvus 客户端处理问题
   - 解决: 检查 embedder.py 和 milvus_client.py
""")


async def main():
    test_file = "/home/guozy/research-rag-agent/data/uploads/Omni JARVIS.pdf"

    if len(sys.argv) > 1:
        test_file = sys.argv[1]

    if not Path(test_file).exists():
        print(f"[!] 文件不存在: {test_file}")
        sys.exit(1)

    await diagnose_pipeline(test_file)


if __name__ == "__main__":
    asyncio.run(main())
