"""auth endpoint 命令测试：显示/切换/重置 API 地址。"""

import pytest

from ret2console.cli import _build_app
from ret2console.config import DEFAULT_API_BASE
from ret2console.repl import dispatch


@pytest.mark.asyncio
async def test_auth_endpoint_show_current():
    app = _build_app()
    assert await dispatch(app, "auth endpoint") == 0
    assert app.config.api_base == DEFAULT_API_BASE


@pytest.mark.asyncio
async def test_auth_endpoint_switch_clears_session():
    app = _build_app()
    app.session.token = "old-token"
    app.api._client = object()  # 模拟已建立连接的 client（旧 base_url）
    assert await dispatch(app, "auth endpoint https://ret2shell.home.undef.work:8443/api --insecure") == 0
    assert app.config.api_base == "https://ret2shell.home.undef.work:8443/api"
    assert app.config.insecure is True
    assert app.api.base_url == "https://ret2shell.home.undef.work:8443/api"
    assert app.api.insecure is True  # 运行时同步，不依赖重启
    assert app.api._client is None  # 强制重建：新 base_url/verify 立即生效
    assert app.api.token is None
    assert app.session.token is None  # 旧 endpoint 的登录态必须清除


@pytest.mark.asyncio
async def test_auth_endpoint_invalid_url():
    app = _build_app()
    assert await dispatch(app, "auth endpoint ftp://bad") == 1
    assert app.config.api_base == DEFAULT_API_BASE  # 未切换


@pytest.mark.asyncio
async def test_auth_endpoint_reset():
    app = _build_app()
    assert await dispatch(app, "auth endpoint https://ret2shell.home.undef.work:8443/api") == 0
    assert await dispatch(app, "auth endpoint reset") == 0
    assert app.config.api_base == DEFAULT_API_BASE
    assert app.config.insecure is False
