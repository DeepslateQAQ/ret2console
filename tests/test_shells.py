"""子 shell 注入测试（SPEC §11）。"""

from pathlib import Path

import pytest

from ret2console.cli import _build_app
from ret2console.repl import dispatch
from ret2console import shells


@pytest.mark.parametrize("name", ["bash", "zsh", "fish"])
def test_rc_contains_submit_and_hook(name):
    rc = {"bash": shells._bash_rc(), "zsh": shells._zsh_rc(), "fish": shells._fish_conf()}[name]
    assert "submit" in rc
    # 提交通道为占位符（launch_shell 时替换为实际可执行路径），命中提交用该命令
    assert "__R2C_BIN__ -c 'submit -'" in rc
    assert r"[A-Za-z0-9_]+[{]" in rc  # flag 正则（字面花括号用字符类，zsh =~ 不认 \{）


def test_rc_submit_cmd_injected():
    """launch_shell 会把占位符替换为实际可执行路径（含引号包裹）。"""
    for rc in (shells._bash_rc("'/abs/ret2console' -c 'submit -'"), shells._zsh_rc("'/abs/ret2console' -c 'submit -'"), shells._fish_conf("'/abs/ret2console' -c 'submit -'")):
        assert "| '/abs/ret2console' -c 'submit -'" in rc
        assert "__R2C_BIN__" not in rc


def test_rc_sources_user_rc_after_hook():
    """注入 rc 应先装 hook，再 source 用户自己的 rc（不覆盖用户配置）。"""
    bash = shells._bash_rc()
    assert 'if [ -f "$HOME/.bashrc" ]; then' in bash
    assert bash.index(". \"$HOME/.bashrc\"") > bash.index("trap __r2c_submit_check DEBUG")
    zsh = shells._zsh_rc()
    assert '${R2C_ORIG_ZDOTDIR:-$HOME}/.zshrc' in zsh
    assert zsh.index("R2C_ORIG_ZDOTDIR") > zsh.index("add-zsh-hook preexec __r2c_preexec")
    fish = shells._fish_conf()
    assert "$R2C_ORIG_XDG_CONFIG_HOME" in fish
    assert 'config.fish' in fish


def test_rc_wraps_command_not_found():
    """用户 rc 注册的 command-not-found 要被包装：flag 静默，其余转交原 handler。"""
    bash = shells._bash_rc()
    assert "command_not_found_handle" in bash and "__r2c_cnf_orig" in bash
    assert bash.index("command_not_found_handle") > bash.index(". \"$HOME/.bashrc\"")
    zsh = shells._zsh_rc()
    assert "command_not_found_handler" in zsh and "__r2c_cnf_orig" in zsh
    fish = shells._fish_conf()
    assert "fish_command_not_found" in fish and "__r2c_cnf_orig" in fish


def test_launch_env_sets_zdotdir_and_context(tmp_path, monkeypatch):
    monkeypatch.setenv("ZDOTDIR", "/orig/zdotdir")
    env = shells._launch_env("zsh", tmp_path, 22, 908)
    assert env["ZDOTDIR"] == str(tmp_path)
    assert env["R2C_ORIG_ZDOTDIR"] == "/orig/zdotdir"
    assert env["R2C_GAME_ID"] == "22"
    assert env["R2C_CHALLENGE_ID"] == "908"
    monkeypatch.setenv("XDG_CONFIG_HOME", "/orig/xdg")
    env = shells._launch_env("fish", tmp_path, None, None)
    assert env["XDG_CONFIG_HOME"] == str(tmp_path / "xconfig")
    assert env["R2C_ORIG_XDG_CONFIG_HOME"] == "/orig/xdg"
    assert "R2C_GAME_ID" not in env


@pytest.mark.asyncio
async def test_launch_shell_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(shells.shutil, "which", lambda _: None)
    app = _build_app()
    assert await shells.launch_shell(app, "bash") == 127


@pytest.mark.asyncio
async def test_launch_shell_injects_env(tmp_path):
    """假 shell：python 脚本打印注入环境变量后退出。"""
    fake = tmp_path / "fakebash"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "print('ZDOTDIR=' + os.environ.get('ZDOTDIR', ''))\n"
        "sys.exit(0)\n"
    )
    fake.chmod(0o755)
    app = _build_app()
    code = await shells.launch_shell(app, "bash", binary=str(fake))
    assert code == 0


@pytest.mark.asyncio
async def test_dispatch_shell_command(tmp_path, monkeypatch):
    """bash 命令注册且可分发。"""
    from ret2console.commands import get_command
    assert get_command("bash") is not None
    assert get_command("zsh") is not None
    assert get_command("fish") is not None


def test_shell_names_platform(monkeypatch):
    """Windows 下隐藏 bash/zsh/fish，换为 cmd/pwsh/powershell。"""
    monkeypatch.setattr(shells.sys, "platform", "win32")
    assert shells.shell_names() == ("cmd", "pwsh", "powershell")
    monkeypatch.setattr(shells.sys, "platform", "linux")
    assert shells.shell_names() == ("bash", "zsh", "fish")
    assert "cmd" not in shells.shell_names()


def test_installed_windows_shell(monkeypatch):
    """Windows shell 仅 Windows 平台可检测：cmd 系统组件必然存在。"""
    monkeypatch.setattr(shells.sys, "platform", "win32")
    assert shells.installed("cmd") == "cmd.exe"
    assert shells.installed("powershell") == "powershell.exe"
    monkeypatch.setattr(shells.sys, "platform", "linux")
    assert shells.installed("cmd") is None
    assert shells.installed("pwsh") is None
    assert shells.installed("powershell") is None


def test_ps_profile_contains_hook():
    """pwsh/powershell 注入脚本：PSReadLine Enter 钩子 + 用户 profile 恢复。"""
    ps = shells._ps_profile()
    assert "Set-PSReadLineKeyHandler -Key Enter" in ps
    assert "AcceptLine" in ps and "RevertLine" in ps
    assert "__R2C_BIN__ -c 'submit -'" in ps
    assert r"[A-Za-z0-9_]+[{]" in ps  # flag 正则
    assert "$PROFILE" in ps  # -NoProfile 启动后手动 source 用户 profile
    assert ps.index("Set-PSReadLineKeyHandler") < ps.index("$PROFILE")


def test_ps_profile_quotes_path():
    """含空格路径的 ps profile 提交命令需单引号包裹（& 已由模板提供）。"""
    ps = shells._ps_profile("'C:/Program Files/ret2console.exe' -c 'submit -'")
    assert "'C:/Program Files/ret2console.exe' -c 'submit -'" in ps
    assert "__R2C_BIN__" not in ps


def test_ps_profile_written_with_utf8_bom():
    """ps profile 必须以 UTF-8 BOM 写入：5.1 按 ANSI 解析无 BOM 文件会乱码。"""
    blob = b"\xef\xbb\xbf" + shells._ps_profile().encode("utf-8")
    assert blob.startswith(b"\xef\xbb\xbf")
    assert "已注入".encode("utf-8") in blob


@pytest.mark.asyncio
async def test_launch_shell_windows(monkeypatch):
    """Windows 启动分支：cmd 原生启动；pwsh 走 -NoProfile -NoExit -File；powershell 走 -Command 点源。

    同时校验题目上下文（R2C_GAME_ID / R2C_CHALLENGE_ID）经 env 传给 PowerShell 子进程。
    """
    import types
    calls: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        proc = types.SimpleNamespace()
        async def wait():
            return 0
        proc.wait = wait
        return proc

    monkeypatch.setattr(shells.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(shells.sys, "platform", "win32")
    app = _build_app()
    app.ctx.set_challenge(22, 908)

    await shells.launch_shell(app, "cmd", binary="cmd.exe")
    args, kwargs = calls[0]
    assert args == ("cmd.exe",)  # 无注入参数

    calls.clear()
    await shells.launch_shell(app, "pwsh", binary="pwsh.exe")
    args, kwargs = calls[0]
    assert args[0] == "pwsh.exe"
    assert args[1:6] == ("-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-File")
    assert Path(args[6]).name == "r2c-profile.ps1"
    # 题目上下文必须传给子 shell，供注入的 submit - 子进程恢复
    assert kwargs.get("env", {}).get("R2C_GAME_ID") == "22"
    assert kwargs.get("env", {}).get("R2C_CHALLENGE_ID") == "908"

    calls.clear()
    await shells.launch_shell(app, "powershell", binary="powershell.exe")
    args, kwargs = calls[0]
    assert args[0] == "powershell.exe"
    assert args[1:6] == ("-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command")
    # 5.1 走 -Command 点源临时脚本，保持 -NoExit 交互
    assert args[6].startswith(". '") and args[6].endswith("r2c-profile.ps1'")
    assert kwargs.get("env", {}).get("R2C_GAME_ID") == "22"
    assert kwargs.get("env", {}).get("R2C_CHALLENGE_ID") == "908"
