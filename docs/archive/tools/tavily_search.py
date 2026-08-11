#!/usr/bin/env python3
"""
归档工具（2026-08-09）：Tavily 搜索工具。

本文件不属于 Backend 运行时，仅保留历史调研用途；仓库不保存 API Key。

功能:
  - search:  执行网页搜索，支持多种过滤和深度参数
  - extract: 提取指定 URL 的网页内容

依赖:
  pip install tavily-python

用法:
  python tavily_search.py search "你的查询" [选项]
  python tavily_search.py extract "https://example.com" [选项]
"""

import argparse
import json
import os
import sys
from typing import Optional

try:
    from tavily import TavilyClient
except ImportError:
    print("错误: 未安装 tavily-python。请运行: pip install tavily-python")
    sys.exit(1)

# API Key 只能由命令行或环境变量提供，禁止写入仓库。
DEFAULT_API_KEY = ""


# ═══════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════

def get_client(api_key: Optional[str] = None) -> TavilyClient:
    """获取 Tavily 客户端，优先级: 命令行 > 环境变量 > 默认值"""
    key = api_key or os.environ.get("TAVILY_API_KEY") or DEFAULT_API_KEY
    if not key:
        print("错误: 未提供 API Key。请通过 --api-key 参数或 TAVILY_API_KEY 环境变量设置。",
              file=sys.stderr)
        sys.exit(1)
    return TavilyClient(api_key=key)


def truncate(text: str, max_len: int = 200) -> str:
    """截断文本，超出部分用 ... 表示"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


# ═══════════════════════════════════════════════════════════════════
# 命令处理
# ═══════════════════════════════════════════════════════════════════

def cmd_search(args) -> None:
    """执行网页搜索"""
    client = get_client(args.api_key)

    kwargs: dict = {
        "query": args.query,
        "search_depth": args.search_depth,
        "max_results": args.max_results,
        "include_answer": args.include_answer,
        "include_raw_content": args.include_raw_content,
    }

    if args.topic:
        kwargs["topic"] = args.topic
    if args.days is not None:
        kwargs["days"] = args.days
    if args.include_domains:
        kwargs["include_domains"] = args.include_domains
    if args.exclude_domains:
        kwargs["exclude_domains"] = args.exclude_domains

    try:
        response = client.search(**kwargs)
        output_search(response, args.output)
    except Exception as e:
        print(f"搜索失败: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_extract(args) -> None:
    """提取网页内容"""
    client = get_client(args.api_key)
    urls = args.urls if isinstance(args.urls, list) else [args.urls]

    try:
        response = client.extract(urls=urls, include_images=args.include_images)
        output_extract(response, args.output)
    except Exception as e:
        print(f"提取失败: {e}", file=sys.stderr)
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════
# 输出格式化
# ═══════════════════════════════════════════════════════════════════

def output_search(data: dict, fmt: str) -> None:
    """格式化输出搜索结果"""
    if fmt == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    # ── 文本格式 ──
    print("=" * 70)
    print(f"🔍 查询: {data.get('query', 'N/A')}")
    print(f"⏱️  响应时间: {data.get('response_time', 'N/A')}s")
    print("=" * 70)

    # AI 摘要
    if data.get("answer"):
        print(f"\n📝 AI 摘要:\n{data['answer']}")

    # 搜索结果
    results = data.get("results", [])
    if results:
        print(f"\n📄 搜索结果 ({len(results)} 条):")
        for i, result in enumerate(results, 1):
            print(f"\n  [{i}] {result.get('title', 'N/A')}")
            print(f"      URL:    {result.get('url', 'N/A')}")
            score = result.get('score')
            if score is not None:
                print(f"      相关性: {score:.4f}")
            content = result.get('content', '')
            if content:
                print(f"      摘要:   {truncate(content, 200)}")
            raw = result.get('raw_content')
            if raw:
                print(f"      原始内容: {truncate(raw, 150)}")
    else:
        print("\n(无搜索结果)")

    # 图片
    images = data.get("images", [])
    if images:
        print(f"\n🖼️  图片 ({len(images)} 张):")
        for img in images[:10]:
            print(f"    {img}")
        if len(images) > 10:
            print(f"    ... 还有 {len(images) - 10} 张")

    print()


def output_extract(data: dict, fmt: str) -> None:
    """格式化输出提取结果"""
    if fmt == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    # ── 文本格式 ──
    print("=" * 70)
    print(f"⏱️  响应时间: {data.get('response_time', 'N/A')}s")
    print("=" * 70)

    results = data.get("results", [])
    if results:
        for i, result in enumerate(results, 1):
            print(f"\n📄 [{i}] {result.get('url', 'N/A')}")
            raw = result.get('raw_content', '')
            if raw:
                print(f"    内容长度: {len(raw)} 字符")
                print(f"    内容预览: {truncate(raw, 500)}")
            imgs = result.get('images', [])
            if imgs:
                print(f"    图片 ({len(imgs)} 张):")
                for img in imgs[:5]:
                    print(f"      {img}")

    failed = data.get("failed_results", [])
    if failed:
        print(f"\n❌ 提取失败的 URL ({len(failed)} 个):")
        for f in failed:
            print(f"    {f}")

    print()


# ═══════════════════════════════════════════════════════════════════
# 命令行入口
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tavily 搜索工具 — 基于 tavily-python SDK",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python tavily_search.py search "Python 最新版本"
  python tavily_search.py search "AI news" --topic news --days 7 --include-answer
  python tavily_search.py search "机器学习" --max-results 5 --search-depth advanced
  python tavily_search.py extract "https://example.com"
  python tavily_search.py extract "https://example.com" --output json
        """,
    )

    parser.add_argument(
        "--api-key",
        help="Tavily API Key（也可通过 TAVILY_API_KEY 环境变量设置）",
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # ── search 子命令 ──
    sp = subparsers.add_parser("search", help="执行网页搜索")
    sp.add_argument("query", help="搜索查询字符串")
    sp.add_argument("--max-results", type=int, default=10,
                    help="最大结果数（默认: 10）")
    sp.add_argument("--search-depth", choices=["basic", "advanced"], default="basic",
                    help="搜索深度（默认: basic）")
    sp.add_argument("--include-answer", action="store_true", default=False,
                    help="包含 AI 生成的摘要答案")
    sp.add_argument("--include-raw-content", action="store_true", default=False,
                    help="包含网页原始内容")
    sp.add_argument("--topic", choices=["general", "news"], default="general",
                    help="搜索主题（默认: general）")
    sp.add_argument("--days", type=int,
                    help="仅搜索最近 N 天的内容（配合 --topic news）")
    sp.add_argument("--include-domains", nargs="+",
                    help="限定搜索的域名列表")
    sp.add_argument("--exclude-domains", nargs="+",
                    help="排除的域名列表")
    sp.add_argument("--output", choices=["text", "json"], default="text",
                    help="输出格式（默认: text）")

    # ── extract 子命令 ──
    ep = subparsers.add_parser("extract", help="提取网页内容")
    ep.add_argument("urls", nargs="+", help="要提取的 URL（可多个，空格分隔）")
    ep.add_argument("--include-images", action="store_true", default=False,
                    help="提取页面中的图片")
    ep.add_argument("--output", choices=["text", "json"], default="text",
                    help="输出格式（默认: text）")

    args = parser.parse_args()

    if args.command == "search":
        cmd_search(args)
    elif args.command == "extract":
        cmd_extract(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
