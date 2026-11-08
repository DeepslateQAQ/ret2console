"""auth show / edit / code / ping 命令测试。"""

import json

import httpx
import pytest

from ret2console.cli import _build_app
from ret2console.repl import dispatch


class AccountBackend:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.profile = {
            "account": "alice",
            "nickname": "爱丽丝",
            "email": "alice@example.com",
            "avatar": "oldhash",
            "institute_id": 2,
            "registered_at": 1700000000,
            "permissions": [0, 1],
            "description": "你好，世界",
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append({"method": request.method, "path": path, "body": request.read()})
        if path.endswith("/account/profile"):
            if request.method == "GET":
                return httpx.Response(200, json=self.profile)
            if request.method == "PATCH":
                body = json.loads(request.read() or b"{}")
                self.profile = {**self.profile, **body}
                return httpx.Response(200, json=self.profile)
        if path.endswith("/media") and request.method == "POST":
            return httpx.Response(200, json={"hash": "newhash"})
        if path.endswith("/account/code"):
            return httpx.Response(200, json={
                "code": 0xAB12CD, "generate_at": 1700000000, "expires_at": 1700000300,
            })
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


def _app(backend: AccountBackend, *, logged_in: bool = True):
    app = _build_app()
    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api", transport=httpx.MockTransport(backend.handler),
    )
    app.api.configure(base_url="http://mock/api", token="jwt" if logged_in else None)
    app.session.token = "jwt" if logged_in else None
    return app


def _patches(backend: AccountBackend) -> list[dict]:
    return [r for r in backend.requests if r["method"] == "PATCH"]


@pytest.mark.asyncio
async def test_auth_show_renders_profile(capsys):
    backend = AccountBackend()
    app = _app(backend)
    assert await dispatch(app, "auth show") == 0
    out = capsys.readouterr().out
    assert "alice" in out
    assert "爱丽丝" in out
    assert "alice@example.com" in out
    assert "你好，世界" in out


@pytest.mark.asyncio
async def test_auth_show_requires_login(capsys):
    backend = AccountBackend()
    app = _app(backend, logged_in=False)
    assert await dispatch(app, "auth show") == 1
    assert "未登录" in capsys.readouterr().out
    assert backend.requests == []


@pytest.mark.asyncio
async def test_auth_edit_description_patches_full_profile():
    backend = AccountBackend()
    app = _app(backend)
    assert await dispatch(app, "auth edit --description 新简介 --yes") == 0
    patches = _patches(backend)
    assert len(patches) == 1
    body = json.loads(patches[0]["body"])
    assert body["description"] == "新简介"
    # 其他字段原样回传，避免 PATCH 清空资料
    assert body["nickname"] == "爱丽丝"
    assert body["email"] == "alice@example.com"
    assert body["permissions"] == [0, 1]


@pytest.mark.asyncio
async def test_auth_edit_avatar_uploads_then_patches(tmp_path):
    avatar = tmp_path / "avatar.png"
    avatar.write_bytes(b"\x89PNG fake")
    backend = AccountBackend()
    app = _app(backend)
    assert await dispatch(app, f"auth edit --avatar {avatar} --yes") == 0
    uploads = [r for r in backend.requests if r["path"].endswith("/media")]
    assert len(uploads) == 1 and uploads[0]["method"] == "POST"
    patches = _patches(backend)
    assert json.loads(patches[0]["body"])["avatar"] == "newhash"


@pytest.mark.asyncio
async def test_auth_edit_remove_avatar():
    backend = AccountBackend()
    app = _app(backend)
    assert await dispatch(app, "auth edit --remove-avatar --yes") == 0
    assert json.loads(_patches(backend)[0]["body"])["avatar"] is None


@pytest.mark.asyncio
async def test_auth_edit_requires_yes_in_non_tty(capsys):
    backend = AccountBackend()
    app = _app(backend)
    assert await dispatch(app, "auth edit --description x") == 1
    assert "需要 --yes" in capsys.readouterr().out
    assert _patches(backend) == []


@pytest.mark.asyncio
async def test_auth_edit_conflicting_flags():
    backend = AccountBackend()
    app = _app(backend)
    assert await dispatch(app, "auth edit --description a --file b") == 1
    assert await dispatch(app, "auth edit --avatar a.png --remove-avatar") == 1
    assert _patches(backend) == []


@pytest.mark.asyncio
async def test_auth_edit_file_reads_markdown(tmp_path):
    path = tmp_path / "desc.md"
    path.write_text("# 标题\n\n内容", encoding="utf-8")
    backend = AccountBackend()
    app = _app(backend)
    assert await dispatch(app, f"auth edit --file {path} --yes") == 0
    assert json.loads(_patches(backend)[0]["body"])["description"] == "# 标题\n\n内容"


@pytest.mark.asyncio
async def test_auth_code_yes(capsys):
    backend = AccountBackend()
    app = _app(backend)
    assert await dispatch(app, "auth code --yes") == 0
    out = capsys.readouterr().out
    assert "AB12CD" in out
    assert "Code" in out and "Expires" in out


@pytest.mark.asyncio
async def test_auth_code_requires_yes_non_tty(capsys):
    backend = AccountBackend()
    app = _app(backend)
    assert await dispatch(app, "auth code") == 1
    assert "需要 --yes" in capsys.readouterr().out
    assert not [r for r in backend.requests if r["path"].endswith("/account/code")]


@pytest.mark.asyncio
async def test_auth_ping(capsys):
    backend = AccountBackend()
    app = _app(backend)
    assert await dispatch(app, "auth ping") == 0
    out = capsys.readouterr().out
    assert "会话有效" in out
    assert "ms" in out
