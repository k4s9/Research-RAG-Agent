#!/usr/bin/env python3
"""Prompt 日志查看工具"""

import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional


PROMPT_LOG_DIR = Path("./data/logs/prompts")


def list_log_files():
    if not PROMPT_LOG_DIR.exists():
        print(f"[!] 日志目录不存在: {PROMPT_LOG_DIR}")
        return []

    log_files = sorted(PROMPT_LOG_DIR.glob("prompt_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return log_files


def view_log_summary():
    print("\n" + "="*70)
    print("  Prompt 日志列表")
    print("="*70)

    log_files = list_log_files()

    if not log_files:
        print("\n[!] 没有找到任何日志文件")
        print(f"    日志目录: {PROMPT_LOG_DIR}")
        return

    print(f"\n{'文件名':<45} {'大小':<10} {'修改时间':<20}")
    print("-"*75)

    for log_file in log_files:
        size = log_file.stat().st_size
        if size < 1024:
            size_str = f"{size}B"
        elif size < 1024 * 1024:
            size_str = f"{size/1024:.1f}KB"
        else:
            size_str = f"{size/(1024*1024):.1f}MB"

        mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
        print(f"{log_file.name:<45} {size_str:<10} {mtime.strftime('%Y-%m-%d %H:%M:%S'):<20}")

    print(f"\n共 {len(log_files)} 个日志文件")


def parse_log_entry(line: str) -> Optional[dict]:
    try:
        return json.loads(line.strip())
    except:
        return None


def view_log_content(filename: str, show_prompts: bool = True, show_responses: bool = True, limit: int = 10):
    log_file = PROMPT_LOG_DIR / filename

    if not log_file.exists():
        print(f"[!] 文件不存在: {log_file}")
        return

    print(f"\n{'='*70}")
    print(f"  日志文件: {filename}")
    print("="*70)

    entries = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            entry = parse_log_entry(line)
            if entry:
                entries.append(entry)

    request_entries = [e for e in entries if e.get("type") == "request"]
    error_entries = [e for e in entries if e.get("type") == "error"]

    print(f"\n统计信息:")
    print(f"  - 总条目数: {len(entries)}")
    print(f"  - 请求数: {len(request_entries)}")
    print(f"  - 错误数: {len(error_entries)}")

    print(f"\n{'-'*70}")
    print("  日志内容预览")
    print("-"*70)

    count = 0
    for entry in entries:
        if entry.get("type") == "request" and show_prompts:
            count += 1
            if count > limit:
                break
            print(f"\n[REQUEST] {entry.get('timestamp')} | Call ID: {entry.get('call_id')}")
            print(f"  Provider: {entry.get('provider')} | Model: {entry.get('model')}")
            print(f"  Session: {entry.get('session_id')} | Projects: {entry.get('project_ids')}")
            print(f"  Temperature: {entry.get('temperature')} | History: {entry.get('history_count')} 条")
            print(f"  Prompt 长度: {entry.get('prompt_length')} 字符")
            print(f"  Prompt 内容:")
            prompt = entry.get("prompt", "")
            for line in prompt.strip().split("\n")[:20]:
                print(f"    {line}")
            if len(prompt.strip().split("\n")) > 20:
                print(f"    ... (共 {len(prompt.strip().split(chr(10)))} 行)")

        elif entry.get("type") == "response" and show_responses:
            count += 1
            if count > limit:
                break
            print(f"\n[RESPONSE] {entry.get('timestamp')} | Call ID: {entry.get('call_id')}")
            print(f"  Success: {entry.get('success')} | Length: {entry.get('response_length')} 字符")
            response = entry.get("response", "")
            print(f"  Response 内容:")
            for line in response.strip().split("\n")[:10]:
                print(f"    {line}")
            if len(response.strip().split("\n")) > 10:
                print(f"    ... (共 {len(response.strip().split(chr(10)))} 行)")

        elif entry.get("type") == "error":
            print(f"\n[ERROR] {entry.get('timestamp')} | Call ID: {entry.get('call_id')}")
            print(f"  Type: {entry.get('error_type')}")
            print(f"  Message: {entry.get('error_message')}")


def search_logs(keyword: str):
    print(f"\n{'='*70}")
    print(f"  搜索日志 (关键词: '{keyword}')")
    print("="*70)

    log_files = list_log_files()
    if not log_files:
        return

    results = []
    for log_file in log_files[:10]:
        with open(log_file, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                entry = parse_log_entry(line)
                if entry and keyword.lower() in json.dumps(entry, ensure_ascii=False).lower():
                    results.append({
                        "file": log_file.name,
                        "line": line_num,
                        "call_id": entry.get("call_id"),
                        "timestamp": entry.get("timestamp"),
                        "type": entry.get("type"),
                        "preview": entry.get("prompt", entry.get("response", ""))[:200]
                    })

    if not results:
        print("\n[!] 没有找到匹配的结果")
        return

    print(f"\n找到 {len(results)} 条匹配结果:\n")
    for i, r in enumerate(results[:20], 1):
        print(f"{i}. [{r['type'].upper()}] {r['timestamp']} | {r['file']}:{r['line']}")
        print(f"   Call ID: {r['call_id']}")
        print(f"   预览: {r['preview'][:100]}...")
        print()


def main():
    parser = argparse.ArgumentParser(description="Prompt 日志查看工具")
    parser.add_argument("--list", "-l", action="store_true", help="列出所有日志文件")
    parser.add_argument("--view", "-v", type=str, help="查看指定日志文件内容")
    parser.add_argument("--search", "-s", type=str, help="搜索包含关键词的日志")
    parser.add_argument("--prompts", "-p", action="store_true", default=True, help="显示 Prompt 内容 (默认开启)")
    parser.add_argument("--no-prompts", action="store_true", help="不显示 Prompt 内容")
    parser.add_argument("--limit", "-n", type=int, default=10, help="限制显示条数")

    args = parser.parse_args()

    if args.list or (not args.view and not args.search):
        view_log_summary()

    if args.view:
        show_prompts = not args.no_prompts
        view_log_content(args.view, show_prompts=show_prompts, limit=args.limit)

    if args.search:
        search_logs(args.search)


if __name__ == "__main__":
    main()
