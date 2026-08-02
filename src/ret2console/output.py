"""输出渲染：Rich console，非 TTY 自动去色；Markdown 内容终端渲染。"""

from __future__ import annotations

import re
import sys
from datetime import datetime

from rich.console import Console
from rich.markdown import Markdown

console = Console(force_terminal=sys.stdout.isatty(), highlight=False)
error_console = Console(stderr=True, force_terminal=sys.stderr.isatty(), highlight=False)


def is_tty() -> bool:
    return sys.stdout.isatty() and sys.stdin.isatty()


_MARKDOWN_PATTERNS = (
    re.compile(r"^\s*#{1,6}\s", re.M),     # ATX 标题
    re.compile(r"^\s*[-*+]\s+", re.M),     # 无序列表
    re.compile(r"^\s*>\s?", re.M),         # 引用块
    re.compile(r"^\s*(```|~~~)", re.M),    # 代码围栏
    re.compile(r"\*\*|__"),                # 加粗/强调
    re.compile(r"\[[^\]]+\]\([^)]+\)"),    # 链接
    re.compile(r"`[^`\n]+`"),              # 行内代码
    re.compile(r"^\s*\|.*\|\s*$", re.M),   # 表格行
)


def looks_like_markdown(text: str) -> bool:
    """内容是否带 Markdown 特征（纯文本保持原样输出，避免误渲染）。"""
    return bool(text) and any(p.search(text) for p in _MARKDOWN_PATTERNS)


def render_markdown(text: str) -> Markdown | str:
    """Markdown 内容返回 rich Markdown 对象，纯文本原样返回。"""
    return Markdown(text) if looks_like_markdown(text) else text


def format_ts(ts) -> str:
    """unix 秒时间戳 → 本地时间（%Y-%m-%d %H:%M）；非时间戳原样返回。"""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError, OSError):
        return str(ts)
