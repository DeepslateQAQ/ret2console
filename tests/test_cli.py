"""cli 退出时 daemon 生命周期测试（exit 即杀 daemon）。"""

import pytest

from ret2console import cli as cli_mod
from ret2console.api.client import ApiClient
from ret2console.wsrx import Wsrx


@pytest.mark.asyncio
async def test_repl_exit_stops_daemon(monkeypatch):
    """REPL 正常退出（exit/EOF）→ 杀掉 daemon。"""
    stopped = []
    monkeypatch.setattr(
        cli_mod, "ensure_wsrx",
        lambda origin=None: Wsrx("daemon", api_port=9999, source="test"),
    )
    monkeypatch.setattr(cli_mod, "stop_daemon", lambda: stopped.append(1))

    async def fake_run_repl(app, history_path=None):
        return 0

    monkeypatch.setattr(cli_mod, "run_repl", fake_run_repl)
    assert await cli_mod._run("repl", []) == 0
    assert stopped == [1]


@pytest.mark.asyncio
async def test_script_mode_keeps_daemon(monkeypatch):
    """`-c`/脚本模式退出后 daemon 保留（后台任务语义）。"""
    stopped = []
    monkeypatch.setattr(
        cli_mod, "ensure_wsrx",
        lambda origin=None: Wsrx("daemon", api_port=9999, source="test"),
    )
    monkeypatch.setattr(cli_mod, "stop_daemon", lambda: stopped.append(1))
    assert await cli_mod._run("script", ["# 注释行"]) == 0
    assert stopped == []


@pytest.mark.asyncio
async def test_repl_desktop_registers_platform_name(monkeypatch):
    """控制源显示名对齐 r2s 平台配置（如「西电 CTF 终端」）。"""
    registered = []
    monkeypatch.setattr(
        cli_mod, "ensure_wsrx",
        lambda origin=None: Wsrx("desktop", api_port=3307, source="test"),
    )
    monkeypatch.setattr(
        cli_mod, "register_control_source",
        lambda st, name="ret2console": registered.append(name) or "approved",
    )

    async def fake_run_repl(app, history_path=None):
        return 0

    monkeypatch.setattr(cli_mod, "run_repl", fake_run_repl)

    async def fake_platform_name(self):
        return "西电 CTF 终端"

    monkeypatch.setattr(ApiClient, "platform_name", fake_platform_name)
    assert await cli_mod._run("repl", []) == 0
    assert registered == ["西电 CTF 终端"]


@pytest.mark.asyncio
async def test_repl_desktop_falls_back_to_ret2console(monkeypatch):
    """平台名请求失败时兜底 ret2console。"""
    registered = []
    monkeypatch.setattr(
        cli_mod, "ensure_wsrx",
        lambda origin=None: Wsrx("desktop", api_port=3307, source="test"),
    )
    monkeypatch.setattr(
        cli_mod, "register_control_source",
        lambda st, name="ret2console": registered.append(name) or "approved",
    )

    async def fake_run_repl(app, history_path=None):
        return 0

    monkeypatch.setattr(cli_mod, "run_repl", fake_run_repl)

    async def fake_platform_name(self):
        return None

    monkeypatch.setattr(ApiClient, "platform_name", fake_platform_name)
    assert await cli_mod._run("repl", []) == 0
    assert registered == ["ret2console"]


@pytest.mark.asyncio
async def test_status_handles_int_permissions(monkeypatch):
    """r2s 的 permissions 是 int 枚举数组，status 不应崩溃（抢注脚本依赖 status）。"""
    from ret2console.commands.basic import cmd_status
    app = cli_mod._build_app()
    app.session.token = "jwt"
    app.session.account = "alice"
    app.session.nickname = "爱丽丝"
    app.session.permissions = [0, 1]
    assert await cmd_status(app, []) == 0


def test_is_admin_int_permissions():
    """r2s 管理员权限是 int 枚举（5=game 6=host 9=devops）。"""
    from ret2console.config import Session
    assert Session(token="t", permissions=[0, 1, 5]).is_admin
    assert Session(token="t", permissions=[0, 1, 9]).is_admin
    assert not Session(token="t", permissions=[0, 1]).is_admin
    assert Session(token="t", permissions=["basic", "host"]).is_admin  # 字符串形式兼容


def test_main_help_flags():
    assert cli_mod.main(["-h"]) == 0
    assert cli_mod.main(["--help"]) == 0
