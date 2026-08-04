"""wsrx 检测与自动下载测试（SPEC §13.1）。"""

import json
import signal
import tarfile
import io
import zipfile

import pytest

from ret2console import wsrx as wsrx_mod
from ret2console.wsrx import Wsrx, detect, download_binary, ensure_wsrx, wsrx_origin


def _tar_blob() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"#!/bin/sh\necho fake-wsrx\n"
        info = tarfile.TarInfo("wsrx")
        info.size = len(data)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_detect_none(monkeypatch):
    monkeypatch.setattr(wsrx_mod.shutil, "which", lambda _: None)
    monkeypatch.setattr(wsrx_mod, "BINARY", __import__("pathlib").Path("/nonexistent/wsrx"))
    monkeypatch.setattr(wsrx_mod, "_find_lock_file", lambda: None)
    assert detect().mode == "none"


def test_detect_path(monkeypatch):
    monkeypatch.setattr(wsrx_mod.shutil, "which", lambda _: "/usr/bin/wsrx")
    assert detect() == Wsrx("cli", binary="/usr/bin/wsrx", source="PATH: /usr/bin/wsrx")


def test_detect_downloaded_binary(monkeypatch, tmp_path):
    binary = tmp_path / "wsrx"
    binary.write_bytes(b"x")
    monkeypatch.setattr(wsrx_mod, "BINARY", binary)
    monkeypatch.setattr(wsrx_mod.shutil, "which", lambda _: None)
    found = detect()
    assert found.mode == "cli" and found.binary == str(binary)


def test_detect_desktop(monkeypatch, tmp_path):
    lock = tmp_path / ".rx.is.alive"
    lock.write_text("8899\n")
    monkeypatch.setattr(wsrx_mod, "_find_lock_file", lambda: lock)
    monkeypatch.setattr(wsrx_mod.shutil, "which", lambda _: None)
    monkeypatch.setattr(wsrx_mod, "BINARY", tmp_path / "no" / "wsrx")

    class FakeResp:
        status_code = 200

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def get(self, url):
            assert "127.0.0.1:8899/version" in url
            return FakeResp()
        def __enter__(self): return self
        def __exit__(self, *a): return None

    monkeypatch.setattr(wsrx_mod.httpx, "Client", FakeClient)
    found = detect()
    assert found.mode == "desktop" and found.api_port == 8899


def test_wsrx_origin_from_api_base():
    assert wsrx_origin("https://ctf.xidian.edu.cn/api") == "https://ctf.xidian.edu.cn"
    assert wsrx_origin("http://localhost:8080/api/") == "http://localhost:8080"
    assert wsrx_origin("") == wsrx_mod.OUR_ORIGIN  # 非法地址兜底


def test_ensure_wsrx_desktop_uses_platform_origin(monkeypatch):
    """GUI 模式下控制源 Origin 跟随配置的平台域名（与网页端同一 scope）。"""
    monkeypatch.setattr(
        wsrx_mod, "detect",
        lambda: Wsrx("desktop", api_port=8899, source="test"),
    )
    found = ensure_wsrx(origin="https://ctf.xidian.edu.cn")
    assert found.mode == "desktop"
    assert found.origin == "https://ctf.xidian.edu.cn"


def test_register_control_source_posts_named_body(monkeypatch):
    """POST /connect 对齐 wsrx-desktop 前端：host 占位、name 显示名、features 数组。"""
    calls = []

    class FakeResp:
        def __init__(self, code):
            self.status_code = code

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def get(self, url, **kw):
            # 第一次 GET（查状态）→ 未注册；第二次（POST 后复查）→ 待确认
            calls.append(("get", kw.get("headers", {})))
            return FakeResp(404 if len(calls) == 1 else 201)

        def post(self, url, **kw):
            calls.append(("post", kw.get("headers", {}), kw.get("json", {})))
            return FakeResp(201)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    monkeypatch.setattr(wsrx_mod.httpx, "Client", FakeClient)
    state = Wsrx("desktop", api_port=8899, origin="https://ctf.xidian.edu.cn")
    out = wsrx_mod.register_control_source(state)
    assert out == "pending"
    kinds = [c[0] for c in calls]
    assert kinds == ["get", "post", "get"]
    _, headers, body = calls[1]
    assert headers["Origin"] == "https://ctf.xidian.edu.cn"
    assert body["host"] == "IN_HEADER"
    assert body["name"] == "ret2console"
    assert body["state"] == "pending"
    assert body["features"] == ["basic"] and isinstance(body["features"], list)
    assert body["settings"] == {}


def test_download_binary(monkeypatch, tmp_path):
    binary = tmp_path / "bin" / "wsrx"
    monkeypatch.setattr(wsrx_mod, "BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(wsrx_mod, "BINARY", binary)
    monkeypatch.setattr(wsrx_mod, "_latest_version", lambda: "9.9.9")
    monkeypatch.setattr(wsrx_mod, "_asset_name", lambda v: f"wsrx-cli-{v}-linux-gnu-x86_64.tar.gz")
    monkeypatch.setattr(wsrx_mod, "RELEASES_API", "unused")

    class FakeResp:
        raise_for_status = lambda self: None
        content = _tar_blob()

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def get(self, url):
            assert "9.9.9" in url
            return FakeResp()
        def __enter__(self): return self
        def __exit__(self, *a): return None

    monkeypatch.setattr(wsrx_mod.httpx, "Client", FakeClient)
    assert download_binary()
    assert binary.is_file()
    assert "fake-wsrx" in binary.read_text()


def _zip_blob(name: str = "wsrx.exe") -> bytes:
    import io as _io
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"target/release/{name}", b"fake-wsrx-win")
    return buf.getvalue()


def test_asset_name_windows(monkeypatch):
    """Windows 平台资产名：windows-msvc-x86_64.zip（x86_64）。"""
    monkeypatch.setattr(wsrx_mod.sys, "platform", "win32")
    monkeypatch.setattr(wsrx_mod.platform, "machine", lambda: "AMD64")
    assert wsrx_mod._asset_name("0.5.17") == "wsrx-cli-0.5.17-windows-msvc-x86_64.zip"
    monkeypatch.setattr(wsrx_mod.platform, "machine", lambda: "arm64")
    assert wsrx_mod._asset_name("0.5.17") is None


def test_asset_name_macos(monkeypatch):
    monkeypatch.setattr(wsrx_mod.sys, "platform", "darwin")
    monkeypatch.setattr(wsrx_mod.platform, "machine", lambda: "arm64")
    assert wsrx_mod._asset_name("0.5.17") == "wsrx-cli-0.5.17-macos-aarch64.zip"


def test_extract_binary_windows_zip():
    """Windows zip 内为 target/release/wsrx.exe，应提取成功。"""
    blob = _zip_blob("wsrx.exe")
    out = wsrx_mod._extract_binary(blob, "wsrx-cli-0.5.17-windows-msvc-x86_64.zip")
    assert out == b"fake-wsrx-win"


def test_extract_binary_macos_zip():
    """macOS zip 内为 target/<triple>/release/wsrx（无 .exe）。"""
    blob = _zip_blob("wsrx")
    out = wsrx_mod._extract_binary(blob, "wsrx-cli-0.5.17-macos-x86_64.zip")
    assert out == b"fake-wsrx-win"


def test_binary_path_windows(monkeypatch):
    """Windows 下 BINARY 带 .exe 后缀（结束后恢复平台并重新加载，避免模块残留 win32 状态）。

    注意：真实 Windows 上 orig_platform 即 win32，恢复后 BINARY 仍是 wsrx.exe。
    """
    import importlib
    orig_platform = wsrx_mod.sys.platform
    try:
        monkeypatch.setattr(wsrx_mod.sys, "platform", "win32")
        importlib.reload(wsrx_mod)
        assert wsrx_mod.BINARY.name == "wsrx.exe"
    finally:
        wsrx_mod.sys.platform = orig_platform
        importlib.reload(wsrx_mod)
        # 恢复后与平台默认一致：win32 → wsrx.exe；其它 → wsrx
        expected = "wsrx.exe" if orig_platform == "win32" else "wsrx"
        assert wsrx_mod.BINARY.name == expected


def test_ensure_starts_daemon_when_missing(monkeypatch, tmp_path):
    binary = tmp_path / "bin" / "wsrx"
    monkeypatch.setattr(wsrx_mod, "BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(wsrx_mod, "BINARY", binary)
    monkeypatch.setattr(wsrx_mod.shutil, "which", lambda _: None)
    monkeypatch.setattr(wsrx_mod, "_find_lock_file", lambda: None)
    monkeypatch.setattr(wsrx_mod, "_load_daemon", lambda: None)
    monkeypatch.setattr(wsrx_mod, "pick_free_port", lambda: 4567)

    def fake_download():
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"#!/bin/sh\n")
        binary.chmod(0o755)
        return True

    monkeypatch.setattr(wsrx_mod, "download_binary", fake_download)
    spawned = []

    def fake_popen(cmd, **kw):
        spawned.append(cmd)
        return type("P", (), {"pid": 4242})()

    monkeypatch.setattr(wsrx_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(wsrx_mod, "_wait_ready", lambda *a, **kw: True)
    found = ensure_wsrx()
    assert found.mode == "daemon"
    assert found.api_port == 4567 and found.secret
    assert spawned and "daemon" in spawned[0]
    assert wsrx_mod.DAEMON_FILE.is_file()


def test_pool_instances_accepts_dict_and_list(monkeypatch):
    """wsrx daemon 的 GET /pool 返回对象（HashMap），desktop 返回数组。"""
    captured = []

    class FakeClient:
        def __init__(self, base_url, headers): pass
        def __enter__(self): return self
        def __exit__(self, *a): return None
        def get(self, path):
            captured.append(path)
            return type("R", (), {
                "raise_for_status": lambda self: None,
                "json": lambda self: {
                    "127.0.0.1:41031": {"local": "127.0.0.1:41031", "remote": "wss://x/api/traffic/abc?port=8000"},
                },
            })()

    monkeypatch.setattr(wsrx_mod, "_pool_client", FakeClient)
    items = wsrx_mod.pool_instances(Wsrx("daemon", api_port=8899))
    assert captured == ["/pool"]
    assert len(items) == 1 and items[0]["local"] == "127.0.0.1:41031"


def test_stop_daemon_kills_and_cleans(monkeypatch, tmp_path):
    daemon_file = tmp_path / "daemon.json"
    daemon_file.write_text(json.dumps({"pid": 12345, "port": 9999, "secret": "s"}))
    monkeypatch.setattr(wsrx_mod, "DAEMON_FILE", daemon_file)
    killed = []
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append((pid, sig)))
    wsrx_mod.stop_daemon()
    assert killed == [(12345, signal.SIGTERM)]
    assert not daemon_file.exists()


def test_stop_daemon_noop_without_state(monkeypatch, tmp_path):
    monkeypatch.setattr(wsrx_mod, "DAEMON_FILE", tmp_path / "daemon.json")
    wsrx_mod.stop_daemon()  # 没有状态文件时不抛异常
