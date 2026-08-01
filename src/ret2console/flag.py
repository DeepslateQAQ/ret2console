"""Flag 识别与安全提交辅助。"""

from __future__ import annotations

import re

# flag{...} / moectf{...} / xxxx{yyyy} 等：字母数字下划线开头，花括号内容非空且不含花括号/换行
FLAG_RE = re.compile(r"^[A-Za-z0-9_]+\{[^{}\n]+\}$")


def extract_flag(line: str) -> str | None:
    """对整行做 trim 后检测是否匹配 flag 格式，返回规范化后的 flag（去除首尾空白）。"""
    stripped = line.strip()
    if FLAG_RE.fullmatch(stripped):
        return stripped
    return None


def should_exclude_from_history(line: str) -> bool:
    """命中 flag 格式的行不应进入命令历史（防泄露）。"""
    return extract_flag(line) is not None
