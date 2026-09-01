"""告警配置跨进程传播 + 无渠道时 fail-closed 的契约。

背景：web_api 以 SERVER_WORKERS=2 多进程运行，alert_manager 是进程内单例。
写配置的进程之外，其余进程会一直用旧渠道；而"没有渠道"时 send_alert_auto
曾经 `return {}`，把一条真实告警无声吞掉。
"""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.alert import alert_config_broadcast, test_delivery
from antcode_core.application.services.alert.alert_delivery_status import (
    ERROR_CHANNEL_DISABLED,
    ERROR_NO_CHANNELS,
    STATUS_NO_CHANNELS,
    STATUS_QUEUED,
    UNDELIVERED_ERROR_CODES,
    undelivered,
)
from antcode_core.application.services.alert.alert_manager import AlertManager
from antcode_core.application.services.alert.alert_service import AlertService
from antcode_core.common import config

SUBSCRIBER_SETTLE_SECONDS = 0.05

# 包的 __init__ 把单例也叫 alert_service，点号字符串会解析到实例而非模块
alert_service_module = importlib.import_module("antcode_core.application.services.alert.alert_service")


def _config() -> dict:
    return AlertService._default_config()


def _service_with_stubbed_db() -> AlertService:
    service = AlertService()
    service._load_config_from_db = AsyncMock(return_value=_config())
    service._apply_config = AsyncMock()
    return service


# ---------------------------------------------------------------------------
# 问题一：配置不传播
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_write_broadcasts_invalidation_to_sibling_processes(monkeypatch) -> None:
    """写入方必须向 Redis 广播失效，否则兄弟 worker 进程永远看不到新配置。"""
    service = _service_with_stubbed_db()
    publish = AsyncMock()
    monkeypatch.setattr(alert_service_module, "publish_alert_config_invalidation", publish)

    await service.reload_config(notify=True)

    publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_happens_even_when_local_config_already_current(monkeypatch) -> None:
    """本进程已提前重载过时也必须广播。

    send_alert 每次都会重载；若按"本进程有变化才广播"，写入接口就会在这种
    竞态下静默跳过通知，兄弟进程继续持有旧配置。
    """
    service = _service_with_stubbed_db()
    publish = AsyncMock()
    monkeypatch.setattr(alert_service_module, "publish_alert_config_invalidation", publish)

    await service.reload_config()  # 模拟 send_alert 路径先行重载
    service._apply_config.reset_mock()

    await service.reload_config(notify=True)

    service._apply_config.assert_not_awaited()  # 本进程确实没有变化
    publish.assert_awaited_once()  # 但通知照发


@pytest.mark.asyncio
async def test_subscriber_reload_does_not_rebroadcast(monkeypatch) -> None:
    """订阅端重载不得再广播，否则两个进程互相唤醒成回环。"""
    service = _service_with_stubbed_db()
    publish = AsyncMock()
    monkeypatch.setattr(alert_service_module, "publish_alert_config_invalidation", publish)
    captured: list = []
    monkeypatch.setattr(
        alert_service_module,
        "start_alert_config_subscriber",
        AsyncMock(side_effect=lambda reload: captured.append(reload)),
    )

    await service.start_invalidation_subscriber()
    await captured[0]()

    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_published_invalidation_makes_subscriber_reload(monkeypatch) -> None:
    """publish 与 subscribe 用同一个频道名，且收到消息就触发重载。"""
    published: list[tuple[str, str]] = []
    subscribed: list[str] = []
    message = {"type": "message", "data": b"1"}

    class _FakePubSub:
        async def subscribe(self, channel):
            subscribed.append(channel)

        async def listen(self):
            for item in ({"type": "subscribe", "data": 1}, message):
                yield item

    class _FakeRedis:
        async def publish(self, channel, payload):
            published.append((channel, payload))

        def pubsub(self):
            return _FakePubSub()

    monkeypatch.setattr(alert_config_broadcast, "get_redis_client", AsyncMock(return_value=_FakeRedis()))
    reload = AsyncMock()

    await alert_config_broadcast.publish_alert_config_invalidation()
    await alert_config_broadcast.start_alert_config_subscriber(reload)
    await asyncio.sleep(SUBSCRIBER_SETTLE_SECONDS)

    assert published[0][0] == subscribed[0]
    # subscribe 确认帧不能被当成数据帧重复触发重载
    reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalidation_channel_follows_redis_namespace(monkeypatch) -> None:
    """频道名必须跟随 REDIS_NAMESPACE。

    共用一台 Redis 的两套部署正是靠 REDIS_NAMESPACE 隔离；写死 ``antcode:``
    时 A 的一次配置写入会唤醒 B 的每个 uvicorn 进程去重载自己的库。
    """
    published: list[tuple[str, str]] = []

    class _FakeRedis:
        async def publish(self, channel, payload):
            published.append((channel, payload))

    monkeypatch.setattr(config.settings, "REDIS_NAMESPACE", "tenant-b")
    monkeypatch.setattr(alert_config_broadcast, "get_redis_client", AsyncMock(return_value=_FakeRedis()))

    await alert_config_broadcast.publish_alert_config_invalidation()

    assert published[0][0] == "tenant-b:alert_config:invalidate"


@pytest.mark.asyncio
async def test_test_alert_rebuilds_channels_from_database(monkeypatch) -> None:
    """/alert/test 必须以 DB 为准重建渠道。

    否则在没处理过配置写入的那个 worker 上，同一次点击会在"已投递"和
    "没有配置任何告警渠道"之间随机跳。
    """
    service = _service_with_stubbed_db()
    service._initialized = True  # 进程早已初始化过，旧逻辑到此就不再读库
    service._config_cache = _config()
    monkeypatch.setattr(test_delivery.alert_manager, "get_enabled_channels", lambda: [])

    await service.send_test_alert("all")

    service._load_config_from_db.assert_awaited_once()


# ---------------------------------------------------------------------------
# 问题二：无渠道时静默丢弃
# ---------------------------------------------------------------------------


def test_send_alert_auto_without_channels_returns_structured_error_code() -> None:
    """没有渠道时必须带结构化错误码返回，不能是空字典。"""
    manager = AlertManager()
    manager._async_enabled = True
    manager._loop = MagicMock()

    result = manager.send_alert_auto("disk full", "CRITICAL", ["CRITICAL"])

    assert result["status"] == STATUS_NO_CHANNELS
    assert result["error_code"] == ERROR_NO_CHANNELS


def test_send_alert_auto_without_channels_logs_the_dropped_alert() -> None:
    """告警发不出去时必须在日志里留下原文，否则这条告警彻底消失。"""
    manager = AlertManager()
    manager._async_enabled = True
    manager._loop = MagicMock()
    records: list[str] = []
    from loguru import logger

    sink_id = logger.add(lambda msg: records.append(msg), level="ERROR")
    try:
        manager.send_alert_auto("nfs mount lost", "CRITICAL", ["CRITICAL"])
    finally:
        logger.remove(sink_id)

    assert any("nfs mount lost" in record for record in records)


def test_every_send_alert_auto_outcome_carries_a_status() -> None:
    """全部分支都必须带 status —— alert_service 直接按下标取用。

    非证伪项：这里覆盖的 shutting_down / not_ready / rate_limited 三个分支在修复
    前就已经带 status，把无渠道分支改回 `return {}` 它照样绿。留着是回归护栏。
    """
    manager = AlertManager()

    manager._shutting_down = True
    assert "status" in manager.send_alert_auto("m", "ERROR", ["ERROR"])

    manager._shutting_down = False
    manager.replace_channels([MagicMock(channel_name="feishu")])
    assert "status" in manager.send_alert_auto("m", "ERROR", ["ERROR"])  # 未就绪

    manager.configure_rate_limit(enabled=True, window=60, max_count=1)
    manager.send_alert_auto("m", "ERROR", ["ERROR"], rate_key="k")
    assert "status" in manager.send_alert_auto("m", "ERROR", ["ERROR"], rate_key="k")  # 限流


def test_undelivered_statuses_all_have_error_codes() -> None:
    """未投递状态漏登记错误码时直接 KeyError，防止悄悄多出无码分支。

    非证伪项：断言的是新增契约模块自身，没有可单独回退的旧行为。
    """
    for status in UNDELIVERED_ERROR_CODES:
        assert undelivered(status)["error_code"]

    with pytest.raises(KeyError):
        undelivered(STATUS_QUEUED)


@pytest.mark.asyncio
async def test_no_channel_status_is_recorded_in_alert_history(monkeypatch) -> None:
    """历史里必须看得出这条告警没发出去，而不是记成 unknown。"""
    service = _service_with_stubbed_db()
    # 换成全新的空 manager：既隔离全局单例被别的用例污染，也让这条断言真的
    # 穿过 send_alert_auto 的无渠道分支，而不是断言一个我自己造的返回值。
    monkeypatch.setattr(alert_service_module, "alert_manager", AlertManager())

    await service.send_alert("db down", level="CRITICAL", source="system")

    assert service.get_history()[0]["status"] == STATUS_NO_CHANNELS


@pytest.mark.asyncio
async def test_no_channel_alert_does_not_break_the_calling_path(monkeypatch) -> None:
    """告警发不出去不能反过来把主流程搞挂——调用方多在异常处理路径上。"""
    service = _service_with_stubbed_db()
    monkeypatch.setattr(alert_service_module, "alert_manager", AlertManager())

    result = await service.send_alert("worker offline", level="CRITICAL", source="worker")

    assert result["error_code"] == ERROR_NO_CHANNELS


@pytest.mark.asyncio
async def test_test_alert_reports_structured_code_instead_of_chinese_text(monkeypatch) -> None:
    """/alert/test 的失败原因要用错误码表达，中文文案只给人看。"""
    service = _service_with_stubbed_db()
    service._initialized = True
    service._config_cache = _config()
    monkeypatch.setattr(test_delivery.alert_manager, "get_enabled_channels", lambda: [])

    result = await service.send_test_alert("all")

    assert result["result"]["error_code"] == ERROR_NO_CHANNELS


@pytest.mark.asyncio
async def test_test_alert_disabled_channel_has_its_own_code(monkeypatch) -> None:
    """ "渠道没配"和"渠道没启用"必须是两个可区分的码。"""
    service = _service_with_stubbed_db()
    service._initialized = True
    service._config_cache = _config()
    monkeypatch.setattr(test_delivery.alert_manager, "get_enabled_channels", lambda: ["email"])

    result = await service.send_test_alert("feishu")

    assert result["result"]["error_code"] == ERROR_CHANNEL_DISABLED
