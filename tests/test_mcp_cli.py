"""r2c mcp CLI 测试：setup 片段、tools/status 输出、凭据覆盖。"""

import pytest

from ret2console.cli import _build_app
from ret2console.mcpserver import cli as mcp_cli


@pytest.mark.asyncio
async def test_setup_claude(capsys):
    assert await mcp_cli.run_setup(agent="claude", profile=None) == 0
    out = capsys.readouterr().out
    assert "claude mcp add --transport stdio" in out
    assert "未写入任何文件" in out


@pytest.mark.asyncio
async def test_setup_omp(capsys):
    assert await mcp_cli.run_setup(agent="omp", profile=None) == 0
    out = capsys.readouterr().out
    assert "~/.omp/agent/mcp.json" in out
    assert '"args": ["mcp", "serve"]' in out


@pytest.mark.asyncio
async def test_setup_profile_is_appended(capsys):
    assert await mcp_cli.run_setup(agent="claude", profile="work") == 0
    out = capsys.readouterr().out
    assert "--profile work" in out
    assert '"--profile", "work"' in out


@pytest.mark.asyncio
async def test_setup_codex(capsys):
    assert await mcp_cli.run_setup(agent="codex", profile=None) == 0
    out = capsys.readouterr().out
    assert "codex mcp add r2c -- r2c mcp serve" in out
    assert "[mcp_servers.r2c]" in out
    assert 'command = "r2c"' in out


@pytest.mark.asyncio
async def test_setup_opencode(capsys):
    assert await mcp_cli.run_setup(agent="opencode", profile=None) == 0
    out = capsys.readouterr().out
    assert "~/.config/opencode/opencode.json" in out
    assert '"type": "local"' in out


@pytest.mark.asyncio
async def test_setup_rejects_invalid_profile(capsys):
    assert await mcp_cli.run_setup(agent="omp", profile="bad name") == 1
    assert "无效的 profile" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_setup_unknown_agent(capsys):
    assert await mcp_cli.run_setup(agent="nope", profile=None) == 1
    assert "未知 Agent" in capsys.readouterr().out


def test_print_tools(capsys):
    mcp_cli.print_tools()
    out = capsys.readouterr().out
    assert "r2c_context" in out
    assert "r2c_challenge_submit" in out


def test_print_status(capsys):
    mcp_cli.print_status(_build_app())
    out = capsys.readouterr().out
    assert "2025-11-25 / 2026-07-28" in out
    assert "33" in out
    assert "未登录" in out


@pytest.mark.asyncio
async def test_main_tools_and_setup(capsys):
    assert await mcp_cli.main(["tools"]) == 0
    assert "r2c_context" in capsys.readouterr().out
    assert await mcp_cli.main(["setup", "--agent", "omp"]) == 0
    assert "mcp.json" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_main_without_subcommand(capsys):
    assert await mcp_cli.main([]) == 1
    assert "用法: r2c mcp" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_main_status_does_not_need_login(capsys):
    assert await mcp_cli.main(["status"]) == 0
    assert "协议版本" in capsys.readouterr().out


def test_build_app_rejects_invalid_url():
    with pytest.raises(ValueError):
        mcp_cli.build_app(url="ftp://example/api")


def test_build_app_rejects_invalid_profile():
    with pytest.raises(ValueError):
        mcp_cli.build_app(profile="bad name")


def test_build_app_url_override_drops_profile_token():
    from ret2console.config import Session
    session = Session.load("default")
    session.token = "profile-token"
    session.save()
    app = mcp_cli.build_app(url="http://example/api")
    assert app.api.base_url == "http://example/api"
    assert app.api.token is None
    assert app.session.token is None


def test_build_app_token_override_keeps_endpoint():
    app = mcp_cli.build_app(token="cli-token")
    assert app.api.token == "cli-token"
    assert app.session.token == "cli-token"
