"""终端图片渲染测试（SPEC §13）。"""

import base64
import io
import subprocess

import pytest

from ret2console import termimg
from ret2console.termimg import (
    _kitty_encode,
    _sixel_encode,
    detect_protocol,
    extract_images,
    is_image_name,
    render_image,
    resolve_media_url,
    show_image,
)


def _png_bytes(size=(24, 12), color=(255, 0, 0)) -> bytes:
    from PIL import Image
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_resolve_media_url():
    base = "https://ctf.xidian.edu.cn/api"
    assert resolve_media_url("/api/media?hash=abc", base) == "https://ctf.xidian.edu.cn/api/media?hash=abc"
    assert resolve_media_url("api/media?hash=abc", base) == "https://ctf.xidian.edu.cn/api/media?hash=abc"
    assert resolve_media_url("https://img.example.com/a.png", base) == "https://img.example.com/a.png"
    assert resolve_media_url("media.webp", "https://host/prefix/api") == "https://host/prefix/media.webp"


def test_extract_images():
    text = "# 标题\n\n![media.webp](/api/media?hash=1) 和 ![图](http://x/y.png) 和 ![z](z.gif)"
    assert extract_images(text) == [
        ("media.webp", "/api/media?hash=1"),
        ("图", "http://x/y.png"),
        ("z", "z.gif"),
    ]


def test_is_image_name():
    assert is_image_name("a.webp")
    assert is_image_name("b.PNG")
    assert not is_image_name("c.zip")


def test_render_image_kitty():
    seq = render_image(_png_bytes(), "kitty", max_cols=40)
    assert seq is not None
    assert seq.startswith("\x1b_Gf=100,a=T,q=2,t=d,s=24,v=12,m=0;")  # 单块：head 即结束
    assert "\x1b_Ga=p,q=2" in seq  # 占位符：随文本滚动，REPL 重绘不丢
    # 所有 chunk 拼接后可解码为 PNG
    payload = ""
    for seg in seq.split("\x1b_G")[1:]:
        params, _, data = seg.partition(";")
        if params.startswith(("f=", "m=")):
            payload += data
    assert base64.b64decode(payload)[:8] == b"\x89PNG\r\n\x1a\n"
    assert seq.endswith("\n")  # 腾位换行


def test_render_image_sixel():
    seq = render_image(_png_bytes(), "sixel", max_cols=40)
    assert seq is not None
    assert seq.startswith("\x1bPq")
    assert seq.endswith("\x1b\\\n")
    assert "#0;2;" in seq  # 调色板定义
    assert "~" in seq  # 全红 6 行像素 → 0x7E（bit0-5 全置位）


def test_render_image_bad_data():
    assert render_image(b"not an image", "kitty") is None
    assert render_image(b"not an image", "sixel") is None
    assert render_image(_png_bytes(), "unknown") is None


def test_render_image_resizes():
    seq = render_image(_png_bytes((2000, 1000)), "kitty", max_cols=40)
    assert "s=240" in seq  # 缩放到最小宽度 240px


def test_sixel_encode_structure():
    from PIL import Image
    img = Image.new("RGB", (8, 13), (10, 200, 30))
    seq = _sixel_encode(img)
    assert seq.startswith("\x1bPq") and seq.endswith("\x1b\\\n")
    assert seq.count("-") == 2  # 13 行 → 3 个 band（6+6+1），2 个换行


def test_kitty_encode_chunks():
    from PIL import Image
    import random
    random.seed(1)
    img = Image.new("RGB", (256, 256))
    img.putdata([(random.randrange(256), random.randrange(256), random.randrange(256)) for _ in range(256 * 256)])
    seq = _kitty_encode(img)
    assert "\x1b_Gm=1;" in seq  # 大数据分块


def test_detect_protocol_non_tty(monkeypatch):
    monkeypatch.setattr(termimg, "is_tty", lambda: False)
    assert detect_protocol() is None


def test_show_image_kitty_uses_kitten(monkeypatch):
    """kitty 协议优先调用 kitten icat，stdin 传入图片数据。"""
    calls = {}
    monkeypatch.setattr(termimg.shutil, "which", lambda c: "/usr/bin/kitten" if c == "kitten" else None)
    monkeypatch.setattr(
        termimg.subprocess, "run",
        lambda args, input, timeout, stderr: (calls.setdefault("args", args), type("P", (), {"returncode": 0})())[1],
    )
    assert show_image(b"pngdata", "kitty") is True
    assert calls["args"][0] == "/usr/bin/kitten"
    assert calls["args"][1] == "icat"


def test_show_image_falls_back_when_kitten_fails(monkeypatch, capsys):
    """kitten 失败时回退内置编码器。"""
    monkeypatch.setattr(termimg.shutil, "which", lambda c: "/usr/bin/kitten" if c == "kitten" else None)
    monkeypatch.setattr(termimg.subprocess, "run", lambda args, input, timeout, stderr: type("P", (), {"returncode": 1})())
    monkeypatch.setattr(termimg, "render_image", lambda d, p, m: "\x1bGFAKE")
    assert show_image(b"pngdata", "kitty") is True
    assert capsys.readouterr().out == "\x1bGFAKE"


def test_show_image_no_kitten_uses_builtin(monkeypatch, capsys):
    monkeypatch.setattr(termimg.shutil, "which", lambda c: None)
    monkeypatch.setattr(termimg, "render_image", lambda d, p, m: "\x1bGFAKE")
    assert show_image(b"pngdata", "kitty") is True
    assert capsys.readouterr().out == "\x1bGFAKE"


def test_show_image_unrenderable(monkeypatch):
    monkeypatch.setattr(termimg.shutil, "which", lambda c: None)
    monkeypatch.setattr(termimg, "render_image", lambda d, p, m: None)
    assert show_image(b"bad", "kitty") is False
    assert show_image(b"bad", "sixel") is False
