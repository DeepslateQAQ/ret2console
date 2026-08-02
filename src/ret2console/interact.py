"""GitHub CLI 风格的交互组件：输入 / 确认 / 选择器。

- 提示: `? 问题: `（青色问号 + 粗体标题）
- 确认: `? 问题 [y/N] `
- 选择: ↑↓ 高亮 + Enter（供 autoplay 使用）
"""

from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory


def _selector_style():
    """选择器样式 = 语法高亮基础样式 + 选中项高亮。"""
    from prompt_toolkit.styles import Style
    from ret2console.lexer import build_style
    return Style(list(build_style().items()) + [("selector", "bold ansiblue")])


def _gh_message(question: str, suffix: str = "") -> HTML:
    return HTML(f"<ansicyan>?</ansicyan> <b>{question}</b>{suffix} ")


async def ask(question: str, *, password: bool = False, default: str | None = None) -> str:
    """gh 风格输入。password=True 时隐藏回显。"""
    session = PromptSession(history=InMemoryHistory())
    suffix = f" <dim>({default})</dim>" if default else ""
    message = _gh_message(question, suffix)
    try:
        return await session.prompt_async(message, is_password=password, default=default or "")
    except (KeyboardInterrupt, EOFError):
        raise


async def confirm(question: str, *, default: bool = False) -> bool:
    """gh 风格确认：`? 问题 [y/N] ` 或 `[Y/n] `。"""
    marker = "[Y/n]" if default else "[y/N]"
    session = PromptSession(history=InMemoryHistory())
    while True:
        answer = (await session.prompt_async(_gh_message(question, f" <dim>{marker}</dim>"))).strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no", ""):
            return False
        from ret2console.output import console
        console.print("[dim]请输入 y 或 n[/dim]")


async def select(title: str, options: list[str]) -> int:
    """gh auth login 风格选择器：↑↓ 移动、输入即过滤、Enter 确认、数字快捷。"""
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Dimension, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    if not options:
        raise ValueError("select 需要至少一个选项")

    state = {"done": False, "index": 0, "filter": ""}

    def visible() -> list[str]:
        kw = state["filter"].lower()
        return [o for o in options if kw in o.lower()]

    def render() -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = [
            ("ansicyan", "? "),
            ("bold", f"{title}  "),
            ("dim", "[Use arrows to move, type to filter]"),
            ("", "\n"),
        ]
        shown = visible()
        if not shown:
            lines += [("dim", "  (无匹配)"), ("", "\n")]
        for i, opt in enumerate(shown):
            if i == state["index"]:
                lines.append(("class:selector", f"> {opt}"))
            else:
                lines.append(("", f"  {opt}"))
            lines.append(("", "\n"))
        if state["filter"]:
            lines += [("dim", f"filter: {state['filter']}"), ("", "\n")]
        lines.append(("", ""))
        return lines

    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        n = len(visible())
        if n:
            state["index"] = (state["index"] - 1) % n

    @kb.add("down")
    def _(event):
        n = len(visible())
        if n:
            state["index"] = (state["index"] + 1) % n

    @kb.add("enter")
    def _(event):
        shown = visible()
        if shown:
            state["index"] = options.index(shown[state["index"]])
            state["done"] = True
            event.app.exit()

    @kb.add("backspace")
    def _(event):
        state["filter"] = state["filter"][:-1]
        state["index"] = 0

    @kb.add(Keys.Any)
    def _(event):
        ch = event.key_sequence[-1].key
        if len(ch) == 1 and ch.isprintable():
            state["filter"] += ch
            state["index"] = 0

    @kb.add("c-c")
    def _(event):
        event.app.exit(exception=KeyboardInterrupt)

    for i in range(1, 10):
        if i <= len(options):
            @kb.add(str(i))
            def _(event, idx=i - 1):
                state["index"] = idx
                state["done"] = True
                event.app.exit()

    control = FormattedTextControl(render)
    layout = Layout(Window(control, height=Dimension(max=12)))
    app = Application(layout=layout, key_bindings=kb, full_screen=False)

    app.style = _selector_style()
    try:
        await app.run_async()
    except KeyboardInterrupt:
        raise
    from ret2console.output import console
    console.print(f"[green]✓[/green] 已选择 {options[state['index']]}")
    return state["index"]
