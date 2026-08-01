"""cat / download(wget) 命令测试。"""

import httpx
import pytest

from ret2console.cli import _build_app
from ret2console.commands import get_command
from ret2console.repl import dispatch


@pytest.mark.asyncio
async def test_cat_reads_vfs_file(tmp_path, monkeypatch):
    from ret2console.vfs import file_node, dir_node

    monkeypatch.chdir(tmp_path)
    app = _build_app()

    # 直接构造 vfs：/contests/3/challenges/5/README
    app.vfs.root.children = [
        dir_node("contests", loader=lambda: [
            dir_node("3", game_id=3, loader=lambda: [
                dir_node("challenges", game_id=3, loader=lambda: [
                    dir_node("5", game_id=3, challenge_id=5, loader=lambda: [
                        file_node("README", game_id=3, challenge_id=5, reader=lambda: "# 题面内容")
                    ])
                ])
            ])
        ])
    ]
    code = await dispatch(app, "cd /contests/3/challenges/5")
    assert code == 0
    code = await dispatch(app, "cat README")
    assert code == 0


@pytest.mark.asyncio
async def test_cat_renders_markdown_meta_file(tmp_path, monkeypatch):
    """notice/info 等 meta 文件内容走 Markdown 渲染。"""
    from ret2console.vfs import file_node
    from ret2console import output as output_mod
    from ret2console.commands import files as files_mod

    monkeypatch.chdir(tmp_path)
    app = _build_app()
    app.vfs.root.children = [
        file_node("notice", reader=lambda: "**开赛通知**\n\n- 周六 10:00 开始\n- 记得看公告")
    ]
    seen = []
    monkeypatch.setattr(files_mod, "render_markdown", lambda t: seen.append(t) or t)
    code = await dispatch(app, "cat /notice")
    assert code == 0
    assert seen and seen[0].startswith("**开赛通知**")


@pytest.mark.asyncio
async def test_cat_without_args_shows_challenge(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = _build_app()
    app.ctx.set_challenge(3, 5)

    class FakeApi:
        async def get_challenge(self, gid, cid):
            return {"title": "签到题", "content": "输出 hello"}
    app.api = FakeApi()  # type: ignore[assignment]
    code = await dispatch(app, "cat")
    assert code == 0


@pytest.mark.asyncio
async def test_download_and_wget_alias(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = _build_app()
    app.ctx.set_challenge(3, 5)

    class FakeBackend:
        def handler(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/file") and request.method == "GET":
                if "file" in request.url.params:
                    return httpx.Response(200, content=b"PK\x03\x04 fake zip")
                return httpx.Response(200, json=[
                    {"folder": "static", "file": "challenge.zip"},
                    {"folder": "static", "file": "README.md"},
                ])
            return httpx.Response(404, json={"message": "unexpected"})

    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api", transport=httpx.MockTransport(FakeBackend().handler)
    )
    app.api.configure(base_url="http://mock/api", token=None)

    code = await dispatch(app, "download challenge.zip")
    assert code == 0
    assert (tmp_path / "challenge.zip").read_bytes() == b"PK\x03\x04 fake zip"

    # wget 别名
    code = await dispatch(app, "wget README.md")
    assert code == 0
    assert (tmp_path / "README.md").read_bytes() == b"PK\x03\x04 fake zip"

    # 文件名冲突自动加 (1)
    code = await dispatch(app, "download challenge.zip")
    assert code == 0
    assert (tmp_path / "challenge (1).zip").exists()


def test_registry_aliases():
    assert get_command("cat") is not None
    assert get_command("wget") is not None
    assert get_command("download") is not None
