"""重建告警渠道期间，对外可见的拓扑不许出现空窗口。

``apply_alert_config`` 过去先把已装配的渠道逐个 remove 掉再逐个装回。发送侧与
重建侧不是同一条执行流——``configure_async`` 会另起一个线程跑 alert_manager
自己的事件循环——落在这个窗口里的告警看到的是一个空拓扑：

- ``send_alert_auto`` 判成 ``no_channels`` 直接返回，告警丢掉；
- ``_send_async`` 更彻底，``named_tasks`` 为空时连一行日志都不留就 return。

配置每次写入都会触发重建（写入方广播 + 每个进程订阅后重载），所以这个窗口在
真实部署里是周期性打开的，不是理论竞态。
"""

from __future__ import annotations

import pytest
from antcode_core.application.services.alert import alert_channel_setup
from antcode_core.application.services.alert.alert_delivery_status import STATUS_NO_CHANNELS
from antcode_core.application.services.alert.alert_manager import AlertManager

FEISHU_KEY = "feishu_webhooks"


def _one_webhook_config() -> dict:
    return {FEISHU_KEY: [{"name": "ops", "url": "https://example.invalid/hook"}]}


@pytest.fixture
def manager(monkeypatch):
    """独立 manager，避免与全局单例互相污染；结束时停掉它起的发送线程。"""
    instance = AlertManager()
    monkeypatch.setattr(alert_channel_setup, "alert_manager", instance)
    yield instance
    instance.shutdown(wait=False)


def _install_probe(monkeypatch, observe):
    """把飞书渠道换成一个"构造时回看当前拓扑"的探针。

    构造发生在重建过程正中间，正是发送侧可能撞进来的那一刻。
    """

    class _ProbeChannel:
        channel_name = "feishu"

        def __init__(self, _values):
            observe()

        def configure_retry(self, *_retry):
            return None

        async def send_alert_for_level(self, *_args):
            return None

    monkeypatch.setattr(
        alert_channel_setup,
        "WEBHOOK_CHANNEL_SPECS",
        ((FEISHU_KEY, _ProbeChannel, "飞书"),),
    )


def test_visible_topology_is_never_empty_while_channels_are_rebuilt(manager, monkeypatch) -> None:
    observed: list[list[str]] = []
    _install_probe(monkeypatch, lambda: observed.append(manager.get_enabled_channels()))

    alert_channel_setup.apply_alert_config(_one_webhook_config())  # 首次装配
    alert_channel_setup.apply_alert_config(_one_webhook_config())  # 重建

    # 首次装配时确实还没有渠道，这是对的；第二次重建时旧拓扑必须还在。
    assert observed[0] == []
    assert observed[-1] == ["feishu"]


def test_alert_raised_during_rebuild_is_not_dropped_as_no_channels(manager, monkeypatch) -> None:
    outcomes: list[dict] = []
    _install_probe(
        monkeypatch,
        lambda: outcomes.append(manager.send_alert_auto("disk full", "ERROR", ["ERROR"])),
    )

    alert_channel_setup.apply_alert_config(_one_webhook_config())
    alert_channel_setup.apply_alert_config(_one_webhook_config())

    assert outcomes[-1]["status"] != STATUS_NO_CHANNELS
