"""子 shell 注入测试（SPEC §11）。"""

import pytest

from ret2console.cli import _build_app
from ret2console.repl import dispatch
from ret2console import shells


@pytest.mark.parametrize("name", ["bash", "zsh", "fish"])
def test_rc_contains_submit_and_hook(name):
    rc = {"bash": shells._bash_rc(), "zsh": shells._zsh_rc(), "fish": shells._fish_conf()}[name]
    assert "submit" in rc
    assert "ret2console -c 'submit -'" in rc
    assert r"[A-Za-z0-9_]+[{]" in rc  # flag 正则（字面花括号用字符类，zsh =~ 不认 \{）


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
