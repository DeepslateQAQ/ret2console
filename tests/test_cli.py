"""CLI lifecycle and general entry-point tests."""

import pytest

from ret2console import cli as cli_mod


class ClosingWsrx:
    def __init__(self, events):
        self.events = events

    async def close(self):
        self.events.append("wsrx")


class ClosingApi:
    def __init__(self, events):
        self.events = events

    async def close(self):
        self.events.append("api")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["repl", "script"])
async def test_all_modes_close_wsrx_before_api(monkeypatch, mode):
    events = []
    app = cli_mod._build_app()
    app.wsrx = ClosingWsrx(events)
    app.api = ClosingApi(events)
    monkeypatch.setattr(cli_mod, "_build_app", lambda: app)

    async def fake_repl(_app):
        return 0

    monkeypatch.setattr(cli_mod, "run_repl", fake_repl)
    assert await cli_mod._run(mode, [] if mode == "repl" else ["# comment"]) == 0
    assert events == ["wsrx", "api"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["repl", "script"])
async def test_all_modes_close_after_command_failure(monkeypatch, mode):
    events = []
    app = cli_mod._build_app()
    app.wsrx = ClosingWsrx(events)
    app.api = ClosingApi(events)
    monkeypatch.setattr(cli_mod, "_build_app", lambda: app)

    async def fail(*args):
        raise RuntimeError("failed")

    monkeypatch.setattr(cli_mod, "run_repl", fail)
    monkeypatch.setattr(cli_mod, "dispatch", fail)
    with pytest.raises(RuntimeError, match="failed"):
        await cli_mod._run(mode, [] if mode == "repl" else ["status"])
    assert events == ["wsrx", "api"]


@pytest.mark.asyncio
async def test_status_handles_int_permissions():
    from ret2console.commands.basic import cmd_status
    app = cli_mod._build_app()
    app.session.token = "jwt"
    app.session.account = "alice"
    app.session.nickname = "爱丽丝"
    app.session.permissions = [0, 1]
    assert await cmd_status(app, []) == 0


def test_is_admin_int_permissions():
    from ret2console.config import Session
    assert Session(token="t", permissions=[0, 1, 5]).is_admin
    assert Session(token="t", permissions=[0, 1, 9]).is_admin
    assert not Session(token="t", permissions=[0, 1]).is_admin
    assert Session(token="t", permissions=["basic", "host"]).is_admin


def test_main_help_flags():
    assert cli_mod.main(["-h"]) == 0
    assert cli_mod.main(["--help"]) == 0


def test_main_version_flags(capsys):
    from ret2console import __version__, __codename__
    for flag in ("-v", "-V", "--version", "version"):
        assert cli_mod.main([flag]) == 0
        out = capsys.readouterr().out
        assert f"Version {__version__}" in out
        assert f"Codename {__codename__}" in out
        assert "AstralFlare-owo/Ret2Console" in out
        assert "Made by 岩酱ovo with FLAG" in out
        assert "GNU General Public License V3" in out
        assert "┏━┓" in out
    assert __version__
    assert __codename__


def test_version_banner_links():
    from ret2console import __version__, __codename__
    from ret2console.repl import _version_banner_text
    text = _version_banner_text()
    spans = {text.plain[span.start:span.end]: span for span in text.spans}
    repo_style = str(spans["AstralFlare-owo/Ret2Console"].style)
    gpl_style = str(spans["GNU General Public License V3"].style)
    assert "https://github.com/AstralFlare-owo/ret2console" in repo_style
    assert "https://www.gnu.org/licenses/gpl-3.0.html" in gpl_style
    assert "cyan" in str(spans[__version__].style)
    assert "magenta" in str(spans[__codename__].style)
    assert "red" in str(spans["FLAG"].style)
    assert "underline" in repo_style
