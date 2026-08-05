"""REPL 主循环的 Ctrl-C 行为：命令执行期中止命令，输入期双击退出。"""

import pytest

from ret2console.commands import EXIT_CODE
from ret2console.state import App
from ret2console import repl as repl_mod


class FakePrompt:
    """按事件序列驱动 prompt_async：字符串=返回输入，异常=抛出。"""

    def __init__(self, events):
        self.events = list(events)

    async def prompt_async(self, *args, **kwargs):
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event


def _patch_repl(monkeypatch, tmp_path, prompts, dispatch_impl=None):
    monkeypatch.setattr(repl_mod, "PromptSession", lambda **kw: FakePrompt(prompts))
    monkeypatch.setattr(repl_mod, "print_splash", lambda: None)

    async def noop(*a, **kw):
        return None

    monkeypatch.setattr(repl_mod, "_welcome", noop)
    monkeypatch.setattr(repl_mod, "build_prompt", lambda app: "")
    monkeypatch.setattr(repl_mod, "dispatch", dispatch_impl or noop)
    return App()


async def _dispatch_ok(app, line):
    return 0


@pytest.mark.asyncio
async def test_double_ctrl_c_exits(tmp_path, monkeypatch, capsys):
    """输入期连续两次 Ctrl-C（中间无输入）→ 退出。"""
    app = _patch_repl(monkeypatch, tmp_path, [KeyboardInterrupt(), KeyboardInterrupt()])
    assert await repl_mod.run_repl(app, history_path=str(tmp_path / "hist")) == 0
    assert "再按一次 Ctrl-C 退出" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_single_ctrl_c_keeps_repl_then_eof(tmp_path, monkeypatch, capsys):
    """输入期一次 Ctrl-C → 仅警告，REPL 继续，EOF 正常退出。"""
    calls = []

    async def dispatch(app, line):
        calls.append(line)
        return 0

    app = _patch_repl(monkeypatch, tmp_path, [KeyboardInterrupt(), "status", EOFError()], dispatch)
    assert await repl_mod.run_repl(app, history_path=str(tmp_path / "hist")) == 0
    assert calls == ["status"]
    assert "再按一次 Ctrl-C 退出" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_input_between_ctrl_c_cancels_exit(tmp_path, monkeypatch, capsys):
    """两次 Ctrl-C 之间有输入提交 → 取消待退出状态，需再按两次才退出。"""
    calls = []

    async def dispatch(app, line):
        calls.append(line)
        return 0

    app = _patch_repl(
        monkeypatch,
        tmp_path,
        [KeyboardInterrupt(), "pwd", KeyboardInterrupt(), KeyboardInterrupt()],
        dispatch,
    )
    assert await repl_mod.run_repl(app, history_path=str(tmp_path / "hist")) == 0
    assert calls == ["pwd"]
    assert capsys.readouterr().out.count("再按一次 Ctrl-C 退出") == 2


@pytest.mark.asyncio
async def test_ctrl_c_aborts_running_command(tmp_path, monkeypatch, capsys):
    """命令执行期间 Ctrl-C → 中止命令、提示已中止、REPL 存活。"""
    calls = []

    async def dispatch(app, line):
        calls.append(line)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return 0

    app = _patch_repl(
        monkeypatch,
        tmp_path,
        ["long", KeyboardInterrupt(), EOFError()],
        dispatch,
    )
    assert await repl_mod.run_repl(app, history_path=str(tmp_path / "hist")) == 0
    assert calls == ["long"]
    out = capsys.readouterr().out
    assert "命令已中止" in out
    assert "再按一次 Ctrl-C 退出" in out


@pytest.mark.asyncio
async def test_exit_command_still_exits(tmp_path, monkeypatch):
    """exit 命令的 EXIT_CODE 路径不受影响。"""

    async def dispatch(app, line):
        return EXIT_CODE

    app = _patch_repl(monkeypatch, tmp_path, ["exit"], dispatch)
    assert await repl_mod.run_repl(app, history_path=str(tmp_path / "hist")) == 0
