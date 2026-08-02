from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text.utils import fragment_list_to_text

from ret2console.lexer import (
    Ret2ConsoleLexer,
    STYLE_COMMAND,
    STYLE_FLAG,
    STYLE_GLOB,
    STYLE_QUOTE,
    STYLE_UNKNOWN,
)


def _tokens(text, is_command):
    lexer = Ret2ConsoleLexer(is_command=is_command)
    return lexer.lex_document(Document(text))(0)


def test_command_highlight():
    tokens = _tokens("ls /contests", lambda w: w == "ls")
    styles = [s for s, _ in tokens]
    assert STYLE_COMMAND in styles


def test_command_style_has_underline():
    from ret2console.lexer import build_style
    assert "underline" in build_style()["r2c-command"]


def test_unknown_command():
    tokens = _tokens("frobnicate", lambda w: False)
    assert any(s == STYLE_UNKNOWN for s, _ in tokens)


def test_flag_line_gold():
    tokens = _tokens("flag{abc123}", lambda w: False)
    assert any(s == STYLE_FLAG for s, _ in tokens)
    assert len(tokens) == 1


def test_quoted_argument_yellow():
    tokens = _tokens("echo 'hello world'", lambda w: w == "echo")
    styles = [s for s, _ in tokens]
    assert STYLE_COMMAND in styles
    assert STYLE_QUOTE in styles


def test_glob_blue():
    tokens = _tokens("ls *.zip", lambda w: w == "ls")
    styles = [s for s, _ in tokens]
    assert STYLE_GLOB in styles


def test_shell_command_highlight_depends_on_install(monkeypatch):
    """shell 命令按本机安装情况高亮：未安装 → 无效命令红色。"""
    from ret2console import shells
    from ret2console.repl import is_valid_command
    monkeypatch.setattr(shells, "installed", lambda name: f"/usr/bin/{name}" if name == "zsh" else None)
    assert is_valid_command("zsh") is True
    assert is_valid_command("bash") is False
    assert is_valid_command("fish") is False
    assert is_valid_command("ls") is True  # 非 shell 命令不受影响
    tokens = _tokens("bash", is_valid_command)
    assert any(s == STYLE_UNKNOWN for s, _ in tokens)
    tokens = _tokens("zsh", is_valid_command)
    assert any(s == STYLE_COMMAND for s, _ in tokens)


def test_splash_art_three_lines_aligned():
    from ret2console.repl import SPLASH_ART
    lines = SPLASH_ART.splitlines()
    assert len(lines) == 3
    assert len({len(l) for l in lines}) == 1  # 三行等宽（列对齐，供按位置着色）
    assert len(lines[0]) == 33


def test_fragments_preserve_whitespace_and_length():
    """lexer 必须保留所有空白（含末尾），保证 fragments 长度与文档一致，
    否则 Tab 补全菜单在尾随空格场景会锚定到行头。"""
    for text in ["auth ", "auth profile ", "cd ", "  ls  -la  ", "ls /tmp "]:
        tokens = _tokens(text, lambda w: w in ("auth", "cd", "ls"))
        assert fragment_list_to_text(tokens) == text
        total = sum(len(t) for _, t in tokens)
        assert total == len(text)


def test_flag_line_keeps_trailing_space():
    tokens = _tokens("flag{abc123} ", lambda w: False)
    assert any(s == STYLE_FLAG for s, _ in tokens)
    assert fragment_list_to_text(tokens) == "flag{abc123} "
