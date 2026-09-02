"""爬虫批次告警评估器。

每条规则都配一正一反：告警最容易做成"永远不触发"或"永远在触发"，
只测一侧等于没测。
"""

from types import SimpleNamespace

import pytest
from antcode_core.application.services.alert.alert_service import AlertService
from antcode_core.domain.models.enums import BatchStatus
from antcode_master.ingester import crawl_batch_alerts as alerts_module
from antcode_master.ingester.crawl_batch_alerts import CrawlBatchAlerts

STALL_AFTER = 1800.0


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


def _batch(public_id="batch-1"):
    return SimpleNamespace(public_id=public_id, name="每日商品页")


def _stat(*, total, active):
    return {"total": total, "active": active}


@pytest.mark.asyncio
async def test_failed_batch_is_alerted(alerts):
    await alerts.notify_settled(_batch(), BatchStatus.FAILED.value, "total=3 success=1 failed=2")

    assert len(alerts.sent) == 1
    assert "batch-1" in alerts.sent[0]["message"]
    assert "failed=2" in alerts.sent[0]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [BatchStatus.COMPLETED.value, BatchStatus.CANCELLED.value],
)
async def test_non_failure_terminal_states_are_not_alerted(alerts, status):
    """反向控制组：正常收尾和用户取消都不该叫醒人。"""
    await alerts.notify_settled(_batch(), status, "total=3 success=3 failed=0")

    assert alerts.sent == []


@pytest.mark.asyncio
async def test_alert_level_is_deliverable_under_default_channel_config(alerts):
    """级别必须落在 alert_service 默认 auto_alert_levels 内。

    否则渠道侧 ERROR_CHANNEL_LEVEL_FILTERED 会把它丢掉——一条永远发不出去的
    告警和没有告警是同一件事，上一版就是这么死的。
    """
    await alerts.notify_settled(_batch(), BatchStatus.FAILED.value, "detail")

    assert alerts.sent[0]["level"] in AlertService._default_config()["auto_alert_levels"]


@pytest.mark.asyncio
async def test_rate_key_is_stable_across_changing_counts(alerts):
    """限流键不能含瞬时计数，否则每条哈希都不同、限流形同虚设。"""
    await alerts.notify_settled(_batch(), BatchStatus.FAILED.value, "failed=2")
    await alerts.notify_settled(_batch(), BatchStatus.FAILED.value, "failed=9")

    assert alerts.sent[0]["rate_key"] == alerts.sent[1]["rate_key"]
    assert alerts.sent[0]["message"] != alerts.sent[1]["message"]


@pytest.mark.asyncio
async def test_delivery_failure_does_not_escape_to_the_caller(monkeypatch):
    """批次终态已在调用点之前落库，webhook 不可达不能连坐后面的推导步骤。"""

    class _Broken:
        async def send_alert(self, **_kwargs):
            raise RuntimeError("webhook unreachable")

    monkeypatch.setattr(alerts_module, "alert_service", _Broken())

    await CrawlBatchAlerts().notify_settled(_batch(), BatchStatus.FAILED.value, "detail")


@pytest.mark.asyncio
async def test_stalled_batch_is_alerted_once(alerts, clock):
    batch = _batch()
    stat = _stat(total=10, active=4)

    await alerts.observe_progress(batch, stat, stall_after=STALL_AFTER)
    clock[0] = STALL_AFTER + 1
    await alerts.observe_progress(batch, stat, stall_after=STALL_AFTER)
    clock[0] = STALL_AFTER * 2
    await alerts.observe_progress(batch, stat, stall_after=STALL_AFTER)

    assert len(alerts.sent) == 1
    assert "停滞" in alerts.sent[0]["message"]


@pytest.mark.asyncio
async def test_first_observation_never_alerts(alerts, clock):
    """反向控制组：master 刚接手时没有基线，不能凭空判定停滞。"""
    clock[0] = STALL_AFTER * 100

    await alerts.observe_progress(_batch(), _stat(total=10, active=4), stall_after=STALL_AFTER)

    assert alerts.sent == []


@pytest.mark.asyncio
async def test_advancing_batch_is_not_alerted(alerts, clock):
    """反向控制组：一直在结算的批次跑多久都不该告警。"""
    batch = _batch()

    for settled in range(6):
        clock[0] = settled * STALL_AFTER
        await alerts.observe_progress(batch, _stat(total=100, active=100 - settled), stall_after=STALL_AFTER)

    assert alerts.sent == []


@pytest.mark.asyncio
async def test_stall_shorter_than_the_window_is_not_alerted(alerts, clock):
    """反向控制组：窗口内的正常慢批次不告警。"""
    batch = _batch()
    stat = _stat(total=10, active=4)

    await alerts.observe_progress(batch, stat, stall_after=STALL_AFTER)
    clock[0] = STALL_AFTER - 1
    await alerts.observe_progress(batch, stat, stall_after=STALL_AFTER)

    assert alerts.sent == []


@pytest.mark.asyncio
async def test_batch_without_active_runs_is_left_to_the_timeout_backstops(alerts, clock):
    """反向控制组：active=0 的不推进批次由超时兜底收敛成 FAILED，不重复告警。"""
    batch = _batch()
    stat = _stat(total=10, active=0)

    await alerts.observe_progress(batch, stat, stall_after=STALL_AFTER)
    clock[0] = STALL_AFTER * 10
    await alerts.observe_progress(batch, stat, stall_after=STALL_AFTER)

    assert alerts.sent == []


@pytest.mark.asyncio
async def test_recovered_then_stalled_again_alerts_again(alerts, clock):
    """停滞→恢复→再停滞要能再报，否则一次告警就把该批次永久静音。"""
    batch = _batch()

    await alerts.observe_progress(batch, _stat(total=10, active=4), stall_after=STALL_AFTER)
    clock[0] = STALL_AFTER + 1
    await alerts.observe_progress(batch, _stat(total=10, active=4), stall_after=STALL_AFTER)

    clock[0] = STALL_AFTER + 2
    await alerts.observe_progress(batch, _stat(total=10, active=3), stall_after=STALL_AFTER)
    clock[0] = STALL_AFTER * 3
    await alerts.observe_progress(batch, _stat(total=10, active=3), stall_after=STALL_AFTER)

    assert [message["rate_key"] for message in alerts.sent] == ["crawl_batch_stalled|batch-1"] * 2


@pytest.mark.asyncio
async def test_retain_drops_batches_that_left_running(alerts, clock):
    """master 长驻，不回收观测点就是一条随批次数增长的泄漏。"""
    await alerts.observe_progress(_batch("batch-1"), _stat(total=10, active=4), stall_after=STALL_AFTER)
    await alerts.observe_progress(_batch("batch-2"), _stat(total=10, active=4), stall_after=STALL_AFTER)

    alerts.retain({"batch-2"})

    assert set(alerts._marks) == {"batch-2"}
