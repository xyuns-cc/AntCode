"""告警接线：评估器必须真的在 master 的批次状态推导 tick 里被求值。

上一版的告警代码本身是对的，死在没有人以正确的方式调它（配置改 web_api 的
单例、求值在 master 的另一个单例）。所以这一组不 mock 评估器，只 mock 投递端，
从 loop 的入口一路走到 alert_service。
"""

from types import SimpleNamespace

import pytest
from antcode_core.domain.models.enums import BatchStatus
from antcode_master.ingester import crawl_batch_alerts as alerts_module
from antcode_master.ingester.crawl_batch_alerts import CrawlBatchAlerts


class _Recorder:
    def __init__(self):
        self.sent = []

    async def send_alert(self, **kwargs):
        self.sent.append(kwargs)


@pytest.fixture
def alerts(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(alerts_module, "alert_service", recorder)
    evaluator = CrawlBatchAlerts()
    evaluator.sent = recorder.sent
    return evaluator


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(alerts_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


class _BatchQuery:
    def __init__(self, updated):
        self._updated = updated

    async def update(self, **_updates):
        return self._updated


class _BatchModel:
    """条件 UPDATE 的替身；``updated=0`` 表示 API 抢先改掉了状态。"""

    updated = 1

    @classmethod
    def filter(cls, **_criteria):
        return _BatchQuery(cls.updated)


async def _noop(*_args, **_kwargs):
    return None


@pytest.fixture
def wired(monkeypatch, alerts):
    from antcode_master.ingester import crawl_batch_status_loop as loop_module

    monkeypatch.setattr(loop_module, "crawl_batch_alerts", alerts)
    monkeypatch.setattr(loop_module, "CrawlBatch", _BatchModel)
    monkeypatch.setattr(_BatchModel, "updated", 1)
    monkeypatch.setattr(loop_module.crawl_batch_dispatcher_service, "handle_batch_event", _noop)
    return loop_module


def _loop_batch(*, age_seconds, seed_count=2):
    from datetime import UTC, datetime, timedelta

    return SimpleNamespace(
        id=1,
        public_id="batch-1",
        name="每日商品页",
        project_id=9,
        seed_urls=[f"https://example.test/{index}" for index in range(seed_count)],
        started_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
        status=BatchStatus.RUNNING.value,
        completed_at=None,
    )


@pytest.mark.asyncio
async def test_loop_alerts_when_a_batch_is_derived_failed(wired, alerts):
    batch = _loop_batch(age_seconds=30)
    stat = {"total": 2, "success": 1, "failed": 1, "cancelled": 0, "active": 0}

    await wired.CrawlBatchStatusLoop()._reconcile_batch(batch, stat)

    assert len(alerts.sent) == 1
    assert "failed=1" in alerts.sent[0]["message"]


@pytest.mark.asyncio
async def test_loop_does_not_alert_when_a_batch_completes(wired, alerts):
    """反向控制组：全成功的批次跑完，运维不该收到任何东西。"""
    batch = _loop_batch(age_seconds=30)
    stat = {"total": 2, "success": 2, "failed": 0, "cancelled": 0, "active": 0}

    await wired.CrawlBatchStatusLoop()._reconcile_batch(batch, stat)

    assert alerts.sent == []


@pytest.mark.asyncio
async def test_loop_does_not_alert_when_the_api_won_the_race(wired, alerts, monkeypatch):
    """反向控制组：CAS 没抢到就不是本 loop 推的终态，不能替它告警。"""
    monkeypatch.setattr(_BatchModel, "updated", 0)
    batch = _loop_batch(age_seconds=30)
    stat = {"total": 2, "success": 1, "failed": 1, "cancelled": 0, "active": 0}

    await wired.CrawlBatchStatusLoop()._reconcile_batch(batch, stat)

    assert alerts.sent == []


@pytest.mark.asyncio
async def test_loop_alerts_on_the_empty_batch_timeout_backstop(wired, alerts):
    batch = _loop_batch(age_seconds=3600)

    await wired.CrawlBatchStatusLoop()._reconcile_batch(batch, None)

    assert len(alerts.sent) == 1
    assert "空转" in alerts.sent[0]["message"]


@pytest.mark.asyncio
async def test_loop_feeds_the_stall_detector_with_its_own_patience_constant(wired, alerts, clock):
    """停滞窗口取 loop 自己的既有常量，不另立一个可以和它漂移的阈值。"""
    loop = wired.CrawlBatchStatusLoop()
    batch = _loop_batch(age_seconds=30)
    stat = {"total": 4, "success": 0, "failed": 0, "cancelled": 0, "active": 4}

    await loop._reconcile_batch(batch, stat)
    clock[0] = loop.INCOMPLETE_DISPATCH_TIMEOUT_SECONDS - 1
    await loop._reconcile_batch(batch, stat)
    assert alerts.sent == []

    clock[0] = loop.INCOMPLETE_DISPATCH_TIMEOUT_SECONDS + 1
    await loop._reconcile_batch(batch, stat)

    assert len(alerts.sent) == 1
    assert "停滞" in alerts.sent[0]["message"]
