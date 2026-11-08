import pytest

from ret2console.commands import get_command, split_line
from ret2console.repl import dispatch
from ret2console.state import App


def test_split_line():
    assert split_line("  cd /contests/3  ") == ["cd", "/contests/3"]


def test_exit_code_escapes_dispatch():
    from ret2console.repl import dispatch as d
    from ret2console.commands import EXIT_CODE
    import asyncio
    app = App()
    assert asyncio.run(d(app, "exit")) == EXIT_CODE
    assert asyncio.run(d(app, "echo a; exit; echo b")) == EXIT_CODE


def test_registry_has_core():
    for name in [
        "help", "exit", "cd", "ls", "auth", "contest", "challenge", "submit", "scoreboard",
        "submissions", "completion", "mcp",
    ]:
        assert get_command(name) is not None, name


@pytest.mark.asyncio
async def test_dispatch_comment():
    app = App()
    assert await dispatch(app, "  # 注释") == 0


@pytest.mark.asyncio
async def test_dispatch_unknown_command():
    app = App()
    assert await dispatch(app, "frobnicate") == 127


@pytest.mark.asyncio
async def test_dispatch_semicolon():
    app = App()
    assert await dispatch(app, "echo a; echo b") == 0


@pytest.mark.asyncio
async def test_flag_submit_requires_login():
    app = App()
    code = await dispatch(app, "flag{secret}")
    assert code == 1  # 未登录提示


@pytest.mark.asyncio
async def test_dispatch_and_operator_skips_on_failure():
    app = App()
    # 第一条失败 → && 后的命令被跳过
    assert await dispatch(app, "frobnicate && echo skipped") == 127
    # 成功 → && 后的命令执行
    assert await dispatch(app, "echo ok && echo also-ok") == 0
    # ; 不受失败影响
    assert await dispatch(app, "frobnicate; echo after") == 0


@pytest.mark.asyncio
async def test_auth_logout_resets_vfs_context():
    """登出后回到 guest：比赛/题目上下文与虚拟路径一并清空（SPEC §7）。"""
    app = App()
    app.ctx.set_path("/contests/1/challenges/2", 1, 2)
    app.session.token = None  # 跳过服务端 logout 请求
    assert await dispatch(app, "auth logout") == 0
    assert app.ctx.vfs_path == "/"
    assert app.ctx.game_id is None
    assert app.ctx.challenge_id is None


def test_split_commands_modes():
    from ret2console.repl import split_commands
    assert split_commands("a && b; c && d") == [
        ("a", "always"), ("b", "if_ok"), ("c", "always"), ("d", "if_ok"),
    ]
    assert split_commands("a;") == [("a", "always")]
    assert split_commands("  ") == []
