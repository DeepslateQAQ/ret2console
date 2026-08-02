"""多 profile 管理：凭证与 endpoint 独立、切换、删除、旧版迁移。"""

import json
import time

import httpx
import pytest

from ret2console.cli import _build_app
from ret2console.config import (
    DEFAULT_API_BASE,
    LEGACY_DIR,
    profile_config_path,
    profile_dir,
    profile_session_path,
)
from ret2console.repl import dispatch


@pytest.mark.asyncio
async def test_profile_create_switch_and_delete():
    app = _build_app()
    assert app.profile_name == "default"
    assert await dispatch(app, "auth profile create work") == 0
    assert app.profile_name == "work"
    assert app.config.api_base == DEFAULT_API_BASE  # 新 profile 独立 endpoint
    assert await dispatch(app, "auth profile list") == 0
    assert await dispatch(app, "auth profile use default") == 0
    assert app.profile_name == "default"
    assert await dispatch(app, "auth profile delete work") == 0
    assert not profile_dir("work").exists()


@pytest.mark.asyncio
async def test_profile_endpoint_independent():
    app = _build_app()
    await dispatch(app, "auth profile create work")
    assert await dispatch(app, "auth endpoint https://ret2shell.home.undef.work:8443/api --insecure") == 0
    assert app.config.api_base == "https://ret2shell.home.undef.work:8443/api"
    # 切回 default：endpoint 互不影响
    await dispatch(app, "auth profile use default")
    assert app.config.api_base == DEFAULT_API_BASE
    assert app.config.insecure is False
    # 切回 work：endpoint 还在
    await dispatch(app, "auth profile use work")
    assert app.config.api_base == "https://ret2shell.home.undef.work:8443/api"
    assert app.config.insecure is True


@pytest.mark.asyncio
async def test_profile_session_independent():
    app = _build_app()
    app.session.token = "token-default"
    app.session.nickname = "甲"
    app.session.save()
    await dispatch(app, "auth profile create work")
    assert not app.session.logged_in  # 新 profile 无凭证
    app.session.token = "token-work"
    app.session.nickname = "乙"
    app.session.save()
    await dispatch(app, "auth profile use default")
    assert app.session.token == "token-default"
    assert app.session.nickname == "甲"
    await dispatch(app, "auth profile use work")
    assert app.session.token == "token-work"
    assert app.session.nickname == "乙"


@pytest.mark.asyncio
async def test_profile_switch_resets_context():
    app = _build_app()
    app.ctx.set_challenge(22, 908)
    await dispatch(app, "auth profile create work")
    assert app.ctx.vfs_path == "/"
    assert app.ctx.game_id is None
    assert app.ctx.challenge_id is None


@pytest.mark.asyncio
async def test_profile_validation():
    app = _build_app()
    assert await dispatch(app, "auth profile create bad/name") == 1
    assert await dispatch(app, "auth profile use missing") == 1
    await dispatch(app, "auth profile create work")
    assert await dispatch(app, "auth profile create work") == 1  # 已存在
    assert await dispatch(app, "auth profile delete work") == 1  # 不能删当前
    assert profile_dir("work").exists()


@pytest.mark.asyncio
async def test_profile_delete_default_allowed_and_recreated():
    """default 可删（换自己的 profile 名），下次 use default 重新实体化。"""
    app = _build_app()
    await dispatch(app, "auth profile create work")
    assert await dispatch(app, "auth profile delete default") == 0
    assert not profile_dir("default").exists()
    assert await dispatch(app, "auth profile list") == 0
    assert await dispatch(app, "auth profile use default") == 0
    assert app.profile_name == "default"
    assert profile_dir("default").is_dir()


@pytest.mark.asyncio
async def test_profile_delete_last_forbidden(monkeypatch):
    """仅剩一个可用 profile 时禁止删除（检查剩余数量而非 default 特判）。"""
    import ret2console.commands.auth as auth_mod
    app = _build_app()
    app.config.active_profile = "ghost"  # 模拟 active 指向已删除目录的异常状态
    monkeypatch.setattr(auth_mod, "list_profiles", lambda: ["work"])
    assert await dispatch(app, "auth profile delete work") == 1


def test_legacy_config_session_migrates_to_default():
    """旧版全局 config.json/session.json → default profile 兜底读取。"""
    LEGACY_DIR.mkdir(parents=True, exist_ok=True)
    (LEGACY_DIR / "config.json").write_text(json.dumps({
        "api_base": "https://old.example/api", "insecure": True,
        "download_dir": "/tmp/dl",
    }), encoding="utf-8")
    (LEGACY_DIR / "session.json").write_text(json.dumps({
        "token": "legacy-token", "account": "old", "nickname": "老人",
        "permissions": [],
    }), encoding="utf-8")
    app = _build_app()
    assert app.config.api_base == "https://old.example/api"
    assert app.config.insecure is True
    assert app.session.logged_in
    assert app.session.token == "legacy-token"
    assert app.session.nickname == "老人"
    # 保存后写入 profile 级文件
    app.session.save()
    data = json.loads(profile_session_path("default").read_text(encoding="utf-8"))
    assert data["token"] == "legacy-token"


class _SwitchBackend:
    """比赛/队伍 mock：一个进行中已加入、一个进行中未加入、一个已结束已加入。"""

    def __init__(self) -> None:
        self.now = int(time.time())
        self.teams = {1: {"id": 10, "name": "杂鱼队"}}  # game 1 已加入

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/game"):
            return httpx.Response(200, json=[[
                {"id": 1, "title": "MoeCTF 2026", "start_at": self.now - 100, "end_at": self.now + 100},
                {"id": 2, "title": "NepCTF 2026", "start_at": self.now - 100, "end_at": self.now + 100},
                {"id": 3, "title": "旧比赛", "start_at": self.now - 500, "end_at": self.now - 100},
            ], 3])
        if path.endswith("/team/self"):
            gid = int(path.split("/")[-3])
            team = self.teams.get(gid)
            return httpx.Response(200, json=team if team else {})
        return httpx.Response(404, json={"message": f"unexpected {path}"})


@pytest.mark.asyncio
async def test_find_joined_active_games():
    from ret2console.contest_switch import find_joined_active_games
    backend = _SwitchBackend()
    app = _build_app()
    app.session.token = "tok"
    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api", transport=httpx.MockTransport(backend.handler),
    )
    app.api.configure(base_url="http://mock/api", token="tok")
    joined = await find_joined_active_games(app)
    assert [(g["id"], t["name"]) for g, t in joined] == [(1, "杂鱼队")]
