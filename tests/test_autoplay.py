"""autoplay 一把梭测试（SPEC §12）。"""

import asyncio
import httpx
import pytest

from ret2console.cli import _build_app
from ret2console.repl import dispatch
from ret2console.commands import autoplay as autoplay_mod
from ret2console.commands.autoplay import _launch_args
from ret2console.wsrx import Wsrx


class AutoBackend:
    def __init__(self, instance: dict | None = None) -> None:
        self.files: list[dict] = [{"file": "a.zip", "folder": "uploads"}]
        self.instance = instance
        self.posted_instance = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/game/3/instance"):
            items = [self.instance] if self.instance else []
            return httpx.Response(200, json=items)
        if path.endswith("/challenge/5/instance") and request.method == "POST":
            self.posted_instance = True
            self.instance = {
                "id": 1, "state": "Running", "traffic": "traffic-nano", "ports": [5000],
                "challenge_id": 5, "challenge_name": "签到",
            }
            return httpx.Response(200, content=b"")  # Ok(())
        if path.endswith("/challenge/5"):
            return httpx.Response(200, json={"title": "签到", "content": "输出 hello"})
        if path.endswith("/challenge/5/file") and "file" in request.url.params:
            return httpx.Response(200, content=b"PK fake")
        if path.endswith("/challenge/5/file"):
            return httpx.Response(200, json=self.files)
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


def _app(backend: AutoBackend):
    app = _build_app()
    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api", transport=httpx.MockTransport(backend.handler),
    )
    app.api.configure(base_url="http://mock/api", token="jwt")
    app.session.token = "jwt"
    return app


def test_launch_args():
    assert _launch_args("codex", "p") == ["codex", "p"]
    assert _launch_args("claude", "p") == ["claude", "p"]
    assert _launch_args("opencode", "p") == ["opencode", "--prompt", "p"]
    assert _launch_args("kimi", "p") == ["kimi"]
    assert _launch_args("omp", "p") == ["omp", "p"]
    assert _launch_args("reasonix", "p") == ["reasonix", "code"]


@pytest.mark.asyncio
async def test_autoplay_requires_challenge_context():
    app = _app(AutoBackend())
    assert await dispatch(app, "autoplay") == 1


@pytest.mark.asyncio
async def test_autoplay_requires_login():
    app = _app(AutoBackend())
    app.ctx.set_challenge(3, 5)
    app.session.clear()
    assert await dispatch(app, "autoplay") == 1


@pytest.mark.asyncio
async def test_autoplay_no_agents(tmp_path, monkeypatch):
    app = _app(AutoBackend())
    app.ctx.set_challenge(3, 5)
    monkeypatch.setattr(autoplay_mod.shutil, "which", lambda _: None)
    assert await dispatch(app, "autoplay") == 1


@pytest.mark.asyncio
async def test_autoplay_builds_taskbook_and_launches(tmp_path, monkeypatch):
    """单 Agent + 确认通过 → 下载附件、生成任务书、启动 Agent。"""
    launched = {}

    class FakeProc:
        pid = 1234
        async def wait(self):
            return 0

    async def fake_exec(*args, env=None, **kw):
        launched["args"] = args
        return FakeProc()

    monkeypatch.setattr(autoplay_mod.shutil, "which", lambda a: f"/usr/bin/{a}" if a == "codex" else None)
    async def fake_confirm(q, **kw):
        return True

    monkeypatch.setattr(autoplay_mod, "confirm", fake_confirm)
    monkeypatch.setattr(autoplay_mod.asyncio, "create_subprocess_exec", fake_exec)
    app = _app(AutoBackend())
    app.config.autoplay_dir = str(tmp_path / "autoplay")
    app.ctx.set_challenge(3, 5)
    assert await dispatch(app, "autoplay") == 0
    assert launched["args"][0] == "codex"
    workdir = tmp_path / "autoplay" / "5"
    taskbook = workdir / "任务书.md"
    assert taskbook.is_file()
    text = taskbook.read_text(encoding="utf-8")
    assert "签到" in text and "输出 hello" in text
    assert (workdir / "a.zip").is_file()
    assert "a.zip" in text


@pytest.mark.asyncio
async def test_autoplay_kimi_copies_prompt_then_launches(tmp_path, monkeypatch):
    """kimi 不支持 TUI 初始消息：打印提示词、确认后裸启动。"""
    launched = {}
    confirms = []

    class FakeProc:
        pid = 1234
        async def wait(self):
            return 0

    async def fake_exec(*args, env=None, **kw):
        launched["args"] = args
        return FakeProc()

    async def fake_confirm(q, **kw):
        confirms.append(q)
        return True

    monkeypatch.setattr(autoplay_mod.shutil, "which", lambda a: f"/usr/bin/{a}" if a == "kimi" else None)
    monkeypatch.setattr(autoplay_mod, "confirm", fake_confirm)
    monkeypatch.setattr(autoplay_mod.asyncio, "create_subprocess_exec", fake_exec)
    app = _app(AutoBackend())
    app.config.autoplay_dir = str(tmp_path / "autoplay")
    app.ctx.set_challenge(3, 5)
    assert await dispatch(app, "autoplay") == 0
    assert launched["args"] == ("kimi",)
    # 两次确认：危险警告 + 复制提示词确认
    assert len(confirms) == 2
    assert "复制" in confirms[1]


@pytest.fixture
def fast_sleep(monkeypatch):
    async def noop(*a, **kw):
        pass
    monkeypatch.setattr(asyncio, "sleep", noop)


def _fake_agent_env(monkeypatch, tmp_path, app, *, wsrx_mode: str | None = "daemon"):
    """autoplay 交互与执行环境的公共 mock；返回捕获器。"""
    launched = {}

    class FakeProc:
        pid = 1234
        async def wait(self):
            return 0

    async def fake_exec(*args, env=None, **kw):
        launched["args"] = args
        return FakeProc()

    monkeypatch.setattr(autoplay_mod.shutil, "which", lambda a: f"/usr/bin/{a}" if a == "codex" else None)
    async def fake_confirm(q, **kw):
        return True

    monkeypatch.setattr(autoplay_mod, "confirm", fake_confirm)
    monkeypatch.setattr(autoplay_mod.asyncio, "create_subprocess_exec", fake_exec)
    app.config.autoplay_dir = str(tmp_path / "autoplay")
    app.ctx.set_challenge(3, 5)
    if wsrx_mode == "daemon":
        app.wsrx = Wsrx("daemon", api_port=9999)
    return launched


@pytest.mark.asyncio
async def test_autoplay_reuses_running_instance(tmp_path, monkeypatch):
    """已有实例：不重复启动，隧道信息写入任务书。"""
    backend = AutoBackend(instance={
        "id": 1, "state": "Running", "traffic": "traffic-nano", "ports": [5000],
        "challenge_id": 5, "challenge_name": "签到",
    })
    app = _app(backend)
    _fake_agent_env(monkeypatch, tmp_path, app)
    tunnels = []
    monkeypatch.setattr(autoplay_mod, "pool_instances", lambda st: [])
    monkeypatch.setattr(autoplay_mod, "pick_free_port", lambda: 40001)
    monkeypatch.setattr(autoplay_mod, "tunnel_add", lambda st, local, remote, label=None: tunnels.append((local, remote)))
    monkeypatch.setattr(autoplay_mod, "save_tunnel", lambda *a, **kw: None)
    assert await dispatch(app, "autoplay") == 0
    assert backend.posted_instance is False
    assert tunnels and tunnels[0][0] == 40001
    text = (tmp_path / "autoplay" / "5" / "任务书.md").read_text(encoding="utf-8")
    assert "## 实例连接" in text
    assert "127.0.0.1:40001" in text


@pytest.mark.asyncio
async def test_autoplay_starts_instance_when_missing(tmp_path, monkeypatch, fast_sleep):
    """无实例：自动 POST 启动并轮询到就绪，连接信息写入任务书。"""
    backend = AutoBackend()  # 初始无实例，POST 后才有
    app = _app(backend)
    _fake_agent_env(monkeypatch, tmp_path, app)
    monkeypatch.setattr(autoplay_mod, "pool_instances", lambda st: [])
    monkeypatch.setattr(autoplay_mod, "pick_free_port", lambda: 40002)
    monkeypatch.setattr(autoplay_mod, "tunnel_add", lambda *a, **kw: None)
    monkeypatch.setattr(autoplay_mod, "save_tunnel", lambda *a, **kw: None)
    assert await dispatch(app, "autoplay") == 0
    assert backend.posted_instance is True
    text = (tmp_path / "autoplay" / "5" / "任务书.md").read_text(encoding="utf-8")
    assert "127.0.0.1:40002" in text


@pytest.mark.asyncio
async def test_autoplay_without_wsrx_notes_no_tunnel(tmp_path, monkeypatch):
    """wsrx 不可用：任务书明确说明未建立隧道，不报错。"""
    backend = AutoBackend(instance={
        "id": 1, "state": "Running", "traffic": "traffic-nano", "ports": [5000],
        "challenge_id": 5, "challenge_name": "签到",
    })
    app = _app(backend)
    _fake_agent_env(monkeypatch, tmp_path, app, wsrx_mode=None)  # Wsrx() mode=none
    assert await dispatch(app, "autoplay") == 0
    text = (tmp_path / "autoplay" / "5" / "任务书.md").read_text(encoding="utf-8")
    assert "## 实例连接" in text
    assert "未建立到靶机的隧道" in text
    assert "127.0.0.1:" not in text
