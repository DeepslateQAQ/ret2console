"""ls 参数解析测试。"""

import asyncio
import pytest

from ret2console.commands.basic import _file_style, _resolve_path, cmd_ls
from ret2console.repl import dispatch
from ret2console.state import App
from ret2console.vfs import dir_node, file_node


@pytest.mark.asyncio
async def test_ls_accepts_short_flags():
    app = App()
    code = await dispatch(app, "ls -a /")
    assert code == 0
    code = await dispatch(app, "ls -l")
    assert code == 0
    code = await dispatch(app, "ls -la /")
    assert code == 0
    code = await dispatch(app, "ls -al")
    assert code == 0


def test_resolve_path():
    app = App()
    app.ctx.set_path("/contests/3", 3, None)
    assert _resolve_path(app, ".") == "/contests/3"
    assert _resolve_path(app, "..") == "/contests"
    assert _resolve_path(app, "/") == "/"
    assert _resolve_path(app, "challenges") == "/contests/3/challenges"


def test_file_style_colors():
    """特殊文件金色/红色，一般文件默认色。"""
    assert _file_style(file_node("flag", reader=lambda: "")) == "bold bright_yellow"
    assert _file_style(file_node("instance", reader=lambda: "")) == "bold bright_yellow"
    assert _file_style(file_node("a.zip", reader=lambda: "")) == "bold bright_red"
    assert _file_style(file_node("README", reader=lambda: "")) is None


@pytest.mark.asyncio
async def test_ls_recursive(monkeypatch):
    """ls -R 递归展开目录树（懒加载逐层）。"""
    from ret2console.output import console
    app = App()
    app.vfs.build(
        contests_loader=lambda: [dir_node("3", game_id=3, meta="比赛3")],
        bulletin_reader=lambda: "公告",
    )
    printed = []
    monkeypatch.setattr(console, "print", lambda *a, **kw: printed.append(a))
    code = await dispatch(app, "ls -R /")
    assert code == 0
    text = " ".join(str(x) for x in printed)
    assert "/:" in text and "contests:" in text and "3:" in text


def test_render_short_challenge_status():
    """题目目录状态：已通过绿色、运行中带 *、一般蓝色。"""
    from rich.text import Text
    from ret2console.commands.basic import _render_short

    nodes = [
        dir_node("900", challenge_id=900),
        dir_node("908", challenge_id=908),
        dir_node("957", challenge_id=957),
        dir_node("docs"),
    ]
    out = _render_short(nodes, solved={900, 908}, running={908})
    assert isinstance(out, Text)
    rendered = out.plain
    assert "900/" in rendered and "908*/" in rendered and "957/" in rendered
    styles = {}
    for s in out.spans:
        styles[rendered[s.start:s.end]] = s.style
    assert styles["900/"] == "bold bright_green"
    assert styles["908*/"] == "bold bright_green"   # 已通过优先，仍带运行中 *
    assert styles["957/"] == "bold bright_blue"
    assert styles["docs/"] == "bold bright_blue"
