"""interact 交互组件测试（SPEC §10）。"""

import pytest

from ret2console.interact import _gh_message, _selector_style


def test_selector_style_builds_with_selector_rule():
    """选择器样式可构建且包含选中项高亮（回归：list | list 曾触发 TypeError）。"""
    style = _selector_style()
    rules = dict(style.style_rules)
    assert rules.get("selector") == "bold ansiblue"


def test_gh_message_is_valid_html():
    """gh 提示消息必须是合法 XML（回归：[/dim] rich 闭合标签曾触发 ExpatError）。"""
    msg = _gh_message("警告：使用一把梭功能可能导致您的亲人遭受损失（物理），是否仍要继续？",
                      " <dim>[y/N]</dim>")
    assert "ansicyan" in str(msg)
    assert "[/dim]" not in str(msg)


def test_select_imports_work():
    """select 的依赖导入不抛错（回归：prompt_toolkit 3.0.52 的 key_binding 不导出 Keys）。"""
    from prompt_toolkit.keys import Keys
    assert Keys.Any
