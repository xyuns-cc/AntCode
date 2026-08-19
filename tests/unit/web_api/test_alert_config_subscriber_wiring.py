"""告警配置失效订阅必须真的被启动。

没有这层守护，propagation 的实现可以全对但没人调用——每个 uvicorn worker
仍旧各自持有一份过期的 alert_manager，bug 原样复现。
"""

import ast
import importlib
import pathlib
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.alert.alert_service import AlertService

WEB_API_STARTUP = "antcode_web_api.lifespan"
MASTER_STARTUP = "antcode_master.__main__"


def _service() -> AlertService:
    service = AlertService()
    service._load_config_from_db = AsyncMock(return_value=AlertService._default_config())
    service._apply_config = AsyncMock()
    return service


def _method_calls(module_name: str) -> set[str]:
    """按 ``接收者.方法`` 收集调用。

    只查方法名会被 system_config_service 的同名 ``start_invalidation_subscriber``
    顶成假绿，所以必须连接收者一起匹配。
    """
    source = pathlib.Path(importlib.import_module(module_name).__file__).read_text(encoding="utf-8")
    return {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
    }


@pytest.mark.asyncio
async def test_initialize_starts_the_invalidation_subscriber(monkeypatch):
    service = _service()
    start = AsyncMock()
    monkeypatch.setattr(service, "start_invalidation_subscriber", start)

    await service.initialize()

    start.assert_awaited_once()


@pytest.mark.asyncio
async def test_initialize_subscribes_even_when_config_was_already_loaded(monkeypatch):
    """先发生过一次 reload 就跳过订阅的话，这个进程会永远收不到配置变更。"""
    service = _service()
    await service.reload_config()  # 模拟 send_alert 路径抢先把 _initialized 置真
    start = AsyncMock()
    monkeypatch.setattr(service, "start_invalidation_subscriber", start)

    await service.initialize()

    start.assert_awaited_once()


@pytest.mark.asyncio
async def test_repeated_initialize_does_not_stack_subscriptions(monkeypatch):
    service = _service()
    start = AsyncMock()
    monkeypatch.setattr(service, "start_invalidation_subscriber", start)

    await service.initialize()
    await service.initialize()

    start.assert_awaited_once()


def test_web_api_startup_initializes_alert_service():
    assert "alert_service.initialize" in _method_calls(WEB_API_STARTUP)


def test_master_startup_initializes_alert_service():
    """master 也要订阅：crawl/重试链路的告警配置同样由 web_api 改。"""
    assert "alert_service.initialize" in _method_calls(MASTER_STARTUP)
