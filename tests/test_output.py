"""Markdown 渲染辅助测试（cat / challenge show / contest doc 共用）。"""

import pytest
from rich.markdown import Markdown

from ret2console.output import looks_like_markdown, render_markdown


@pytest.mark.parametrize("text", [
    "# 比赛通知",
    "## 规则\n1. 不准爆破",
    "- 条目一\n- 条目二",
    "> 引用",
    "```bash\nflag{test}\n```",
    "**加粗** 和 *斜体*",
    "点击[链接](https://example.com)",
    "行内 `code`",
    "| a | b |\n|---|---|",
])
def test_looks_like_markdown_true(text):
    assert looks_like_markdown(text), repr(text)


@pytest.mark.parametrize("text", [
    "",
    "输出 hello",
    "flag{abcdef} 提交成功",
    "比赛 #3  状态: running\n\n简介",
    "  1. team_a  100 分",
    "附件 a.zip（uploads/）：请使用 download 命令下载",
    "队伍: test\n队长: admin",
    "state: running  ports: 22, 80",
])
def test_looks_like_markdown_false(text):
    assert not looks_like_markdown(text), repr(text)


def test_render_markdown_returns_rich_object():
    assert isinstance(render_markdown("# 标题\n\n内容"), Markdown)


def test_render_markdown_keeps_plain_text():
    text = "输出 hello"
    assert render_markdown(text) is text
