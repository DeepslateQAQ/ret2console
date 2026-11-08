"""completion 命令测试（脚本内容 / 落盘 / 覆盖保护）。"""

import pytest

from ret2console.cli import _build_app
from ret2console.repl import dispatch


def _app():
    return _build_app()


@pytest.mark.asyncio
async def test_completion_bash(capsys):
    assert await dispatch(_app(), "completion bash") == 0
    out = capsys.readouterr().out
    assert "_r2c_completions" in out
    assert "complete -F _r2c_completions r2c ret2console" in out
    assert "auth" in out and "login" in out
    assert "challenge" in out and "files" in out


@pytest.mark.asyncio
async def test_completion_zsh(capsys):
    assert await dispatch(_app(), "completion zsh") == 0
    out = capsys.readouterr().out
    assert out.startswith("#compdef r2c ret2console")
    assert "_describe 'command' _r2c_commands" in out
    assert "_r2c \"$@\"" in out


@pytest.mark.asyncio
async def test_completion_fish(capsys):
    assert await dispatch(_app(), "completion fish") == 0
    out = capsys.readouterr().out
    assert "complete -c r2c -f" in out
    assert "__fish_use_subcommand" in out
    assert "__fish_seen_subcommand_from auth" in out


@pytest.mark.asyncio
async def test_completion_output_refuses_overwrite(capsys, tmp_path):
    target = tmp_path / "r2c.bash"
    target.write_text("old", encoding="utf-8")
    assert await dispatch(_app(), f"completion bash --output {target}") == 1
    assert "文件已存在" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_completion_output_force_overwrites(tmp_path):
    target = tmp_path / "nested" / "r2c.bash"
    assert await dispatch(_app(), f"completion bash --output {target} --force") == 0
    assert "_r2c_completions" in target.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_completion_dash_writes_stdout(capsys):
    assert await dispatch(_app(), "completion fish --output -") == 0
    assert "complete -c r2c" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_completion_rejects_unknown_shell(capsys):
    assert await dispatch(_app(), "completion tcsh") == 1
    assert "未知 shell" in capsys.readouterr().out
