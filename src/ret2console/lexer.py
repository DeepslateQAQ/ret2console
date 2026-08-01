"""语法高亮（zsh-syntax-highlighting 风格, SPEC §6）。

命令绿 bold / 未知命令红 / 路径蓝下划线 / 注释灰 / flag 整行金色。
"""

from __future__ import annotations

from typing import Callable

from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text.base import StyleAndTextTuples
from prompt_toolkit.lexers import Lexer

from ret2console.flag import extract_flag

STYLE_COMMAND = "class:r2c-command"
STYLE_UNKNOWN = "class:r2c-unknown"
STYLE_PATH = "class:r2c-path"
STYLE_COMMENT = "class:r2c-comment"
STYLE_FLAG = "class:r2c-flag"
STYLE_QUOTE = "class:r2c-quote"
STYLE_GLOB = "class:r2c-glob"
STYLE_DEFAULT = ""


def build_style() -> dict[str, str]:
    return {
        # 对齐 zsh-syntax-highlighting 默认主题（ANSI 8 色），取亮色以适配深色终端
        "r2c-command": "bold ansigreen underline",  # fg=green,underline
        "r2c-unknown": "bold ansired",         # fg=red,bold
        "r2c-path": "underline ansiblue",      # path + 蓝色下划线（SPEC 定案）
        "r2c-comment": "bold ansibrightblack", # fg=black,bold → 亮灰
        "r2c-flag": "bold ansiyellow",         # 扩展：flag 整行金色
        "r2c-quote": "ansiyellow",             # fg=yellow（引号内容）
        "r2c-glob": "ansiblue",                # fg=blue（globbing）
    }


class Ret2ConsoleLexer(Lexer):
    def __init__(
        self,
        is_command: Callable[[str], bool] | None = None,
        is_path: Callable[[str], bool] | None = None,
    ) -> None:
        self._is_command = is_command or (lambda _: False)
        self._is_path = is_path or (lambda _: False)

    def lex_document(self, document: Document) -> Callable[[int], StyleAndTextTuples]:
        def get_line(lineno: int) -> StyleAndTextTuples:
            if lineno != 0:
                return [("", "")]
            text = document.text
            if not text:
                return [("", "")]
            if extract_flag(text):
                return [(STYLE_FLAG, text)]
            return self._lex_line(text)

        return get_line

    def _lex_line(self, text: str) -> StyleAndTextTuples:
        if text.lstrip().startswith("#"):
            return [(STYLE_COMMENT, text)]
        stripped = text.lstrip()
        lead = text[: len(text) - len(stripped)]
        # 按空白分词，保持每个 token 的原始边界
        tokens: list[tuple[int, int]] = []
        i = 0
        n = len(stripped)
        while i < n:
            while i < n and stripped[i].isspace():
                i += 1
            start = i
            while i < n and not stripped[i].isspace():
                i += 1
            if start < i:
                tokens.append((start, i))
        out: StyleAndTextTuples = []
        if lead:
            out.append(("", lead))
        for idx, (s, e) in enumerate(tokens):
            token = stripped[s:e]
            if idx == 0:
                style = STYLE_COMMAND if self._is_command(token) else STYLE_UNKNOWN
            elif token.startswith("/") or self._is_path(token):
                style = STYLE_PATH
            elif token.startswith(("'", '"')) or token.startswith("$'"):
                style = STYLE_QUOTE
            elif any(ch in token for ch in "*?[]"):
                style = STYLE_GLOB
            else:
                style = STYLE_DEFAULT
            out.append((style, stripped[s:e]))
            if idx < len(tokens) - 1:
                out.append(("", stripped[e : tokens[idx + 1][0]]))
        return out
