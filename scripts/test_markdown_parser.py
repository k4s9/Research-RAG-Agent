#!/usr/bin/env python3
"""Markdown 文档诊断工具

该工具对 Markdown 文档进行全面诊断，验证其解析质量和分块效果。
包括语法检查、格式验证、结构分析、问题识别和优化建议。
"""

import asyncio
import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.ingest.markdown_parser import EnhancedMarkdownParser
from src.core.ingest.markdown_cleaner import MarkdownCleaner
from src.core.ingest.markdown_chunker import MarkdownChunker


@dataclass
class DiagnosticReport:
    file_path: str
    timestamp: str
    overall_score: float = 0.0
    issues_count: int = 0
    warnings_count: int = 0

    syntax_issues: List[Dict] = field(default_factory=list)
    format_issues: List[Dict] = field(default_factory=list)
    structure_issues: List[Dict] = field(default_factory=list)
    chunking_issues: List[Dict] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)

    file_stats: Dict = field(default_factory=dict)
    structure_stats: Dict = field(default_factory=dict)
    chunking_stats: Dict = field(default_factory=dict)


class MarkdownSyntaxChecker:
    """Markdown 语法检查器"""

    def __init__(self):
        self.issues = []

    def check(self, content: str) -> List[Dict]:
        self.issues = []

        self._check_heading_syntax(content)
        self._check_link_syntax(content)
        self._check_code_block_syntax(content)
        self._check_list_syntax(content)
        self._check_table_syntax(content)
        self._check_image_syntax(content)

        return self.issues

    def _add_issue(self, severity: str, message: str, line: int = None, context: str = ""):
        self.issues.append({
            "severity": severity,
            "type": "syntax",
            "message": message,
            "line": line,
            "context": context
        })

    def _check_heading_syntax(self, content: str):
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            if line.startswith('#'):
                if not re.match(r'^#{1,6}\s+\S', line):
                    if re.match(r'^#+\s*$', line):
                        self._add_issue("warning", "标题缺少内容", i, line.strip())
                    elif not line.startswith('##'):
                        self._add_issue("info", "一级标题建议使用 Markdown 最高级", i, line.strip())

    def _check_link_syntax(self, content: str):
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            if '](' in line:
                if not re.search(r'\[[^\]]+\]\([^)]+\)', line):
                    self._add_issue("error", "链接语法不完整", i, line.strip()[:50])
            if line.startswith('](') or line.endswith('[]'):
                self._add_issue("error", "链接引用格式错误", i, line.strip())

    def _check_code_block_syntax(self, content: str):
        code_blocks = re.findall(r'```[\s\S]*?```', content)
        if len(code_blocks) % 2 != 0:
            self._add_issue("error", "代码块未正确闭合", context=f"发现 {len(code_blocks)} 个开始标记")

        if '```' in content:
            lines = content.split('\n')
            for i, line in enumerate(lines, 1):
                if line.strip().startswith('```') and len(line.strip()) > 3:
                    lang = line.strip()[3:]
                    if not re.match(r'^[a-zA-Z0-9]+$', lang):
                        self._add_issue("warning", f"代码块语言标识可能无效: '{lang}'", i)

    def _check_list_syntax(self, content: str):
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith('- ') or stripped.startswith('* '):
                if len(stripped) < 3:
                    self._add_issue("warning", "列表项内容可能为空", i, stripped)
            if re.match(r'^\d+\.\s', stripped):
                if not re.match(r'^\d+\.\s+\S', stripped):
                    self._add_issue("warning", "有序列表格式可能不正确", i, stripped)

    def _check_table_syntax(self, content: str):
        lines = content.split('\n')
        table_rows = []
        for i, line in enumerate(lines, 1):
            if '|' in line:
                table_rows.append((i, line))
            elif table_rows:
                if not all('|' in r[1] for r in table_rows):
                    for row_i, row in table_rows:
                        self._add_issue("warning", "表格行格式不一致", row_i, row[1][:50])
                table_rows = []

        table_count = content.count('|')
        if table_count > 0:
            header_row = re.search(r'\|[\s\w-]+\|[\s\w-]+\|', content)
            separator_row = re.search(r'\|[\s:]+\|[\s:]+\|', content)
            if header_row and not separator_row:
                self._add_issue("warning", "表格缺少分隔行", context="表头后应添加 --- 分隔行")

    def _check_image_syntax(self, content: str):
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            if '![' in line:
                if not re.search(r'!\[[^\]]*\]\([^)]+\)', line):
                    self._add_issue("error", "图片语法不完整", i, line.strip()[:50])


class MarkdownFormatValidator:
    """Markdown 格式验证器 - 对照项目要求验证"""

    def __init__(self):
        self.issues = []

    def validate(self, content: str) -> List[Dict]:
        self.issues = []

        self._check_project_requirements(content)
        self._check_encoding(content)
        self._check_line_endings(content)
        self._check_trailing_whitespace(content)
        self._check_whitespace_consistency(content)

        return self.issues

    def _add_issue(self, severity: str, message: str, context: str = ""):
        self.issues.append({
            "severity": severity,
            "type": "format",
            "message": message,
            "context": context
        })

    def _check_project_requirements(self, content: str):
        if len(content) > 10 * 1024 * 1024:
            self._add_issue("error", "文件大小超过 10MB 限制", f"当前: {len(content) / 1024 / 1024:.2f}MB")

        lines = content.split('\n')
        if len(lines) > 10000:
            self._add_issue("warning", "文件行数过多，建议拆分", f"当前: {len(lines)} 行")

        if content.count('\r\n') > len(lines) * 0.5:
            self._add_issue("info", "建议使用 Unix 风格换行符 (LF)")

    def _check_encoding(self, content: str):
        try:
            content.encode('utf-8')
        except UnicodeEncodeError as e:
            self._add_issue("error", f"UTF-8 编码错误: {e}")

        try:
            content.encode('latin-1')
        except UnicodeEncodeError:
            pass

    def _check_line_endings(self, content: str):
        crlf_count = content.count('\r\n')
        lf_count = content.count('\n') - crlf_count

        if crlf_count > 0 and lf_count > 0:
            self._add_issue("warning", "混用不同换行符 (CRLF 和 LF)")

    def _check_trailing_whitespace(self, content: str):
        lines = content.split('\n')
        trailing_lines = []
        for i, line in enumerate(lines, 1):
            if line.rstrip() != line and line.strip():
                trailing_lines.append(i)

        if len(trailing_lines) > 10:
            self._add_issue("warning", f"存在 {len(trailing_lines)} 行带尾部空格", f"行号: {trailing_lines[:5]}...")
        elif len(trailing_lines) > 0:
            self._add_issue("info", f"存在 {len(trailing_lines)} 行带尾部空格")

    def _check_whitespace_consistency(self, content: str):
        if '\t' in content:
            tab_count = content.count('\t')
            self._add_issue("info", f"发现 {tab_count} 个制表符，建议使用空格")


class MarkdownStructureAnalyzer:
    """Markdown 结构分析器"""

    def __init__(self):
        self.stats = {}

    def analyze(self, content: str) -> Tuple[Dict, List[Dict]]:
        stats = {
            "total_chars": len(content),
            "total_lines": len(content.split('\n')),
            "total_words": len(content.split()),
            "headings": {"h1": 0, "h2": 0, "h3": 0, "h4": 0, "h5": 0, "h6": 0},
            "tables": 0,
            "code_blocks": 0,
            "inline_code": 0,
            "links": 0,
            "images": 0,
            "lists": {"ordered": 0, "unordered": 0},
            "blockquotes": 0,
            "horizontal_rules": 0,
            "paragraphs": 0,
            "empty_lines": 0
        }

        issues = []

        lines = content.split('\n')
        in_code_block = False

        for line in lines:
            stripped = line.strip()

            if stripped.startswith('```'):
                in_code_block = not in_code_block
                stats["code_blocks"] += 1
                continue

            if not in_code_block:
                if stripped.startswith('# '):
                    stats["headings"]["h1"] += 1
                    if len(stripped) < 3:
                        issues.append({"severity": "error", "type": "structure", "message": "H1 标题为空", "context": stripped})
                elif stripped.startswith('## '):
                    stats["headings"]["h2"] += 1
                elif stripped.startswith('### '):
                    stats["headings"]["h3"] += 1
                elif stripped.startswith('#### '):
                    stats["headings"]["h4"] += 1
                elif stripped.startswith('##### '):
                    stats["headings"]["h5"] += 1
                elif stripped.startswith('###### '):
                    stats["headings"]["h6"] += 1

                if '|' in stripped:
                    stats["tables"] += stripped.count('|') // 2

                stats["inline_code"] += stripped.count('`') // 2
                stats["links"] += stripped.count('[')
                stats["images"] += stripped.count('![')

                if re.match(r'^\d+\.\s', stripped):
                    stats["lists"]["ordered"] += 1
                elif stripped.startswith('- ') or stripped.startswith('* '):
                    stats["lists"]["unordered"] += 1

                if stripped.startswith('>'):
                    stats["blockquotes"] += 1

                if stripped in ['---', '***', '___']:
                    stats["horizontal_rules"] += 1

                if stripped == '':
                    stats["empty_lines"] += 1
                elif not any(stripped.startswith(x) for x in ['#', '-', '*', '>', '|', '```']):
                    if not re.match(r'^\s', stripped):
                        stats["paragraphs"] += 1

        self._analyze_heading_hierarchy(stats, issues)
        self._analyze_chunking_compatibility(stats, issues)

        self.stats = stats
        return stats, issues

    def _analyze_heading_hierarchy(self, stats: Dict, issues: List):
        if stats["headings"]["h1"] == 0:
            issues.append({"severity": "warning", "type": "structure", "message": "文档缺少一级标题 (H1)"})
        elif stats["headings"]["h1"] > 1:
            issues.append({"severity": "warning", "type": "structure", "message": f"文档包含 {stats['headings']['h1']} 个一级标题，建议只有一个"})

        h2_count = stats["headings"]["h2"]
        h3_count = stats["headings"]["h3"]
        if h3_count > 0 and h2_count == 0:
            issues.append({"severity": "warning", "type": "structure", "message": "存在 H3 标题但缺少 H2 标题，层级结构不连续"})

    def _analyze_chunking_compatibility(self, stats: Dict, issues: List):
        total_content_elements = (
            stats["headings"]["h1"] + stats["headings"]["h2"] +
            stats["headings"]["h3"] + stats["headings"]["h4"] +
            stats["tables"] + stats["code_blocks"] +
            stats["lists"]["ordered"] + stats["lists"]["unordered"] +
            stats["blockquotes"]
        )

        if total_content_elements == 0:
            issues.append({"severity": "warning", "type": "structure", "message": "文档缺少结构性元素，可能影响分块效果"})

        avg_paragraph_length = stats["total_chars"] / max(stats["paragraphs"], 1)
        if avg_paragraph_length > 4000:
            issues.append({"severity": "info", "type": "structure", "message": f"段落平均长度较长 ({avg_paragraph_length:.0f} 字符)，可能需要分块"})


class MarkdownChunkingAnalyzer:
    """Markdown 分块分析器"""

    def __init__(self):
        self.issues = []

    def analyze_chunking(self, content: str, chunks: List[Dict]) -> Tuple[Dict, List[Dict]]:
        self.issues = []

        stats = {
            "total_chunks": len(chunks),
            "chunk_sizes": [],
            "min_chunk_size": 0,
            "max_chunk_size": 0,
            "avg_chunk_size": 0,
            "ideal_range_chunks": 0,
            "too_small_chunks": 0,
            "too_large_chunks": 0,
            "cross_section_chunks": 0,
            "headerless_chunks": 0,
            "semantic_coherence_score": 0.0
        }

        if not chunks:
            self.issues.append({"severity": "error", "type": "chunking", "message": "未能生成任何 chunk"})
            return stats, self.issues

        chunk_sizes = [len(c.get("content", "")) for c in chunks]
        stats["chunk_sizes"] = chunk_sizes
        stats["min_chunk_size"] = min(chunk_sizes) if chunk_sizes else 0
        stats["max_chunk_size"] = max(chunk_sizes) if chunk_sizes else 0
        stats["avg_chunk_size"] = sum(chunk_sizes) / len(chunk_sizes) if chunk_sizes else 0

        ideal_min, ideal_max = 200, 4000
        for size in chunk_sizes:
            if size < ideal_min:
                stats["too_small_chunks"] += 1
            elif size > ideal_max:
                stats["too_large_chunks"] += 1
            else:
                stats["ideal_range_chunks"] += 1

        self._check_cross_section_chunks(content, chunks)
        self._check_semantic_coherence(chunks)
        self._check_metadata_preservation(chunks)

        ideal_chunks = stats["ideal_range_chunks"]
        total_chunks = stats["total_chunks"]
        stats["semantic_coherence_score"] = (ideal_chunks / total_chunks * 100) if total_chunks > 0 else 0

        return stats, self.issues

    def _check_cross_section_chunks(self, content: str, chunks: List[Dict]):
        headings = []
        for line in content.split('\n'):
            if re.match(r'^#{1,6}\s+', line):
                headings.append(line.strip())

        for i, chunk in enumerate(chunks):
            chunk_content = chunk.get("content", "")
            heading_count = sum(1 for h in headings if h in chunk_content)

            if heading_count > 1:
                self.issues.append({
                    "severity": "warning",
                    "type": "chunking",
                    "message": f"Chunk {i+1} 跨越多个章节 (包含 {heading_count} 个标题)",
                    "context": f"内容预览: {chunk_content[:100]}..."
                })

            page_num = chunk.get("metadata", {}).get("page_num", "N/A")
            if page_num == "N/A" or page_num is None:
                self.issues.append({
                    "severity": "warning",
                    "type": "chunking",
                    "message": f"Chunk {i+1} 缺少页面元数据"
                })

    def _check_semantic_coherence(self, chunks: List[Dict]):
        for i, chunk in enumerate(chunks):
            content = chunk.get("content", "")

            sentences = re.split(r'[.!?。！？]', content)
            if len(sentences) > 1:
                first_sentence_words = set(sentences[0].split()) if sentences[0].split() else set()
                last_sentence_words = set(sentences[-1].split()) if sentences[-1].split() else set()

                if first_sentence_words and last_sentence_words:
                    overlap = len(first_sentence_words & last_sentence_words)
                    if overlap < 2 and len(first_sentence_words) > 5 and len(last_sentence_words) > 5:
                        self.issues.append({
                            "severity": "info",
                            "type": "chunking",
                            "message": f"Chunk {i+1} 首尾句子主题可能不连贯",
                            "context": f"首: {sentences[0][:30]}... 末: {sentences[-1][:30]}..."
                        })

    def _check_metadata_preservation(self, chunks: List[Dict]):
        for i, chunk in enumerate(chunks):
            metadata = chunk.get("metadata", {})

            if "chunk_type" not in metadata:
                self.issues.append({
                    "severity": "info",
                    "type": "chunking",
                    "message": f"Chunk {i+1} 缺少 chunk_type 元数据"
                })

            if "page_num" not in metadata:
                self.issues.append({
                    "severity": "info",
                    "type": "chunking",
                    "message": f"Chunk {i+1} 缺少 page_num 元数据"
                })


class MarkdownDiagnosticTool:
    """Markdown 文档诊断工具主类"""

    def __init__(self):
        self.syntax_checker = MarkdownSyntaxChecker()
        self.format_validator = MarkdownFormatValidator()
        self.structure_analyzer = MarkdownStructureAnalyzer()
        self.chunking_analyzer = MarkdownChunkingAnalyzer()
        self.parser = EnhancedMarkdownParser()
        self.cleaner = MarkdownCleaner()
        self.chunker = MarkdownChunker(chunk_size=384, overlap=128)

    async def diagnose(self, file_path: str) -> DiagnosticReport:
        report = DiagnosticReport(
            file_path=file_path,
            timestamp=datetime.now().isoformat()
        )

        if not Path(file_path).exists():
            report.format_issues.append({
                "severity": "error",
                "type": "format",
                "message": f"文件不存在: {file_path}"
            })
            return report

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            report.format_issues.append({
                "severity": "error",
                "type": "format",
                "message": f"文件读取失败: {str(e)}"
            })
            return report

        print(f"\n{'='*70}")
        print(f"  Markdown 诊断报告: {Path(file_path).name}")
        print('='*70)

        print("\n[1/5] 语法检查...")
        syntax_issues = self.syntax_checker.check(content)
        report.syntax_issues = syntax_issues
        print(f"      发现 {len(syntax_issues)} 个语法问题")

        print("\n[2/5] 格式验证...")
        format_issues = self.format_validator.validate(content)
        report.format_issues = format_issues
        print(f"      发现 {len(format_issues)} 个格式问题")

        print("\n[3/5] 结构分析...")
        structure_stats, structure_issues = self.structure_analyzer.analyze(content)
        report.structure_stats = structure_stats
        report.structure_issues = structure_issues
        print(f"      结构统计: {structure_stats.get('total_words', 0)} 词, {structure_stats.get('paragraphs', 0)} 段落")

        print("\n[4/5] 解析与分块...")
        try:
            parsed_result = await self.parser.parse(file_path)
            cleaned_result = self.cleaner.clean(parsed_result)
            chunk_results = self.chunker.chunk(cleaned_result)

            chunks = [{"content": c.content, "content_type": c.content_type, "metadata": c.metadata} for c in chunk_results]
            chunking_stats, chunking_issues = self.chunking_analyzer.analyze_chunking(content, chunks)

            report.chunking_stats = {
                **chunking_stats,
                "chunk_preview": [c.content[:100] for c in chunk_results[:3]]
            }
            report.chunking_issues = chunking_issues

            print(f"      生成 {len(chunk_results)} 个 chunks")

        except Exception as e:
            report.chunking_issues.append({
                "severity": "error",
                "type": "chunking",
                "message": f"解析或分块失败: {str(e)}"
            })
            print(f"      解析失败: {str(e)}")

        print("\n[5/5] 生成报告...")
        report.issues_count = (
            len(report.syntax_issues) +
            len(report.format_issues) +
            len(report.structure_issues) +
            len(report.chunking_issues)
        )
        report.warnings_count = sum(
            1 for i in [report.syntax_issues, report.format_issues, report.structure_issues, report.chunking_issues]
            for issue in i if issue.get("severity") in ["warning", "info"]
        )

        report.overall_score = self._calculate_score(report)
        report.recommendations = self._generate_recommendations(report)

        report.file_stats = {
            "size_bytes": len(content),
            "size_kb": len(content) / 1024,
            "encoding": "utf-8"
        }

        return report

    def _calculate_score(self, report: DiagnosticReport) -> float:
        score = 100.0

        error_count = sum(
            1 for i in [report.syntax_issues, report.format_issues, report.structure_issues, report.chunking_issues]
            for issue in i if issue.get("severity") == "error"
        )
        score -= error_count * 10

        warning_count = sum(
            1 for i in [report.syntax_issues, report.format_issues, report.structure_issues, report.chunking_issues]
            for issue in i if issue.get("severity") == "warning"
        )
        score -= warning_count * 2

        info_count = sum(
            1 for i in [report.syntax_issues, report.format_issues, report.structure_issues, report.chunking_issues]
            for issue in i if issue.get("severity") == "info"
        )
        score -= info_count * 0.5

        return max(0.0, min(100.0, score))

    def _generate_recommendations(self, report: DiagnosticReport) -> List[str]:
        recommendations = []

        if report.overall_score < 70:
            recommendations.append("文档质量评分较低，建议进行全面审查和修订")

        if any(i.get("type") == "chunking" and i.get("severity") == "error" for i in report.chunking_issues):
            recommendations.append("分块过程出现错误，请检查 MarkdownParser 实现")

        if report.structure_stats.get("headings", {}).get("h1", 0) == 0:
            recommendations.append("添加文档一级标题 (H1) 以改善文档结构")

        if report.chunking_stats.get("too_small_chunks", 0) > report.chunking_stats.get("total_chunks", 1) * 0.3:
            recommendations.append("过小的 chunks 比例偏高，建议增加 chunk_size 或优化分块策略")

        if report.chunking_stats.get("too_large_chunks", 0) > report.chunking_stats.get("total_chunks", 1) * 0.3:
            recommendations.append("过大的 chunks 比例偏高，建议降低 chunk_size 或增加重叠")

        if report.structure_stats.get("tables", 0) > 0:
            recommendations.append("文档包含表格，建议确保表格格式规范（表头后有分隔行）")

        if not recommendations:
            recommendations.append("文档整体质量良好，无需重大修改")

        return recommendations

    def print_report(self, report: DiagnosticReport):
        print("\n" + "="*70)
        print("  诊断结果汇总")
        print("="*70)

        print(f"\n总体评分: {report.overall_score:.1f}/100")
        print(f"问题总数: {report.issues_count} (错误: {report.issues_count - report.warnings_count}, 警告: {report.warnings_count})")

        print(f"\n文件统计:")
        print(f"  - 大小: {report.file_stats.get('size_kb', 0):.2f} KB")
        print(f"  - 字符数: {report.structure_stats.get('total_chars', 0)}")
        print(f"  - 词数: {report.structure_stats.get('total_words', 0)}")
        print(f"  - 行数: {report.structure_stats.get('total_lines', 0)}")

        print(f"\n结构统计:")
        headings = report.structure_stats.get('headings', {})
        print(f"  - 标题: H1={headings.get('h1', 0)}, H2={headings.get('h2', 0)}, H3={headings.get('h3', 0)}")
        print(f"  - 段落: {report.structure_stats.get('paragraphs', 0)}")
        print(f"  - 表格: {report.structure_stats.get('tables', 0)}")
        print(f"  - 代码块: {report.structure_stats.get('code_blocks', 0)}")
        print(f"  - 列表: 有序={report.structure_stats.get('lists', {}).get('ordered', 0)}, 无序={report.structure_stats.get('lists', {}).get('unordered', 0)}")

        print(f"\n分块统计:")
        print(f"  - 生成 chunks: {report.chunking_stats.get('total_chunks', 0)}")
        print(f"  - 字符数范围: {report.chunking_stats.get('min_chunk_size', 0)} - {report.chunking_stats.get('max_chunk_size', 0)}")
        print(f"  - 平均字符数: {report.chunking_stats.get('avg_chunk_size', 0):.1f}")
        print(f"  - 理想范围: {report.chunking_stats.get('ideal_range_chunks', 0)}, 过小: {report.chunking_stats.get('too_small_chunks', 0)}, 过大: {report.chunking_stats.get('too_large_chunks', 0)}")

        all_issues = (
            [("语法", i) for i in report.syntax_issues] +
            [("格式", i) for i in report.format_issues] +
            [("结构", i) for i in report.structure_issues] +
            [("分块", i) for i in report.chunking_issues]
        )

        if all_issues:
            print(f"\n问题详情:")
            for category, issue in all_issues[:20]:
                severity_icon = {"error": "✗", "warning": "⚠", "info": "ℹ"}.get(issue.get("severity"), "-")
                context = issue.get("context", "")[:40] if issue.get("context") else ""
                print(f"  [{severity_icon}] [{category}] {issue.get('message', '')} {context}")

        if report.recommendations:
            print(f"\n优化建议:")
            for i, rec in enumerate(report.recommendations, 1):
                print(f"  {i}. {rec}")

        print("\n" + "="*70)

    def export_json(self, report: DiagnosticReport, output_path: str):
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(asdict(report), f, ensure_ascii=False, indent=2)
        print(f"\n[✓] JSON 报告已导出: {output_path}")


async def main():
    parser = argparse.ArgumentParser(description="Markdown 文档诊断工具")
    parser.add_argument("file", type=str, help="Markdown 文件路径")
    parser.add_argument("--export", "-e", type=str, help="导出 JSON 报告")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")

    args = parser.parse_args()

    if not Path(args.file).exists():
        print(f"[!] 文件不存在: {args.file}")
        sys.exit(1)

    tool = MarkdownDiagnosticTool()
    report = await tool.diagnose(args.file)
    tool.print_report(report)

    if args.export:
        tool.export_json(report, args.export)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python test_markdown_parser.py <markdown文件路径> [--export report.json]")
        print("示例: python test_markdown_parser.py ./data/uploads/readme.md")
        sys.exit(1)

    asyncio.run(main())
