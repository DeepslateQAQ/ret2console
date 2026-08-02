"""--help 统一拦截与 man 手册命令测试。"""

import pytest

from ret2console.cli import _build_app
from ret2console.repl import dispatch


@pytest.mark.asyncio
async def test_help_flag_on_any_command(capsys):
    app = _build_app()
    assert await dispatch(app, "ls --help") == 0
    assert await dispatch(app, "auth -h") == 0
    assert await dispatch(app, "challenge --help") == 0
    assert "ls" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_help_flag_subcommand(capsys):
    app = _build_app()
    assert await dispatch(app, "auth login --help") == 0
    assert "auth" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_man_index(capsys):
    app = _build_app()
    assert await dispatch(app, "man") == 0
    out = capsys.readouterr().out
    assert "man <命令>" in out


@pytest.mark.asyncio
async def test_man_command_page(capsys):
    app = _build_app()
    assert await dispatch(app, "man auth") == 0
    out = capsys.readouterr().out
    assert "auth" in out and "login" in out


@pytest.mark.asyncio
async def test_man_subcommand_page(capsys):
    app = _build_app()
    assert await dispatch(app, "man auth login") == 0
    assert "登录" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_man_unknown():
    app = _build_app()
    assert await dispatch(app, "man frobnicate") == 1
    assert await dispatch(app, "man auth bogus") == 1
