from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models.enums import BatchStatus
from antcode_master.ingester import crawl_batch_status_loop as loop_module
from antcode_master.ingester.crawl_batch_status_loop import CrawlBatchStatusLoop


class _BatchQuery:
    async def update(self, **updates):
        _BatchModel.updates.append(updates)
        return 1


class _BatchModel:
    updates = []

    @classmethod
    def reset(cls):
        cls.updates = []

    @classmethod
    def filter(cls, **_criteria):
        return _BatchQuery()


def _batch(*, age_seconds, seed_count=2):
    return SimpleNamespace(
        id=1,
        public_id="batch-1",
        project_id=9,
        seed_urls=[f"https://example.test/{index}" for index in range(seed_count)],
        started_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
        status=BatchStatus.RUNNING.value,
        completed_at=None,
    )


@pytest.fixture(autouse=True)
def _fake_batch_model(monkeypatch):
    _BatchModel.reset()
    monkeypatch.setattr(
        "antcode_master.ingester.crawl_batch_status_loop.CrawlBatch",
        _BatchModel,
    )


@pytest.fixture(autouse=True)
def dispatch_events(monkeypatch):
    """未派完的批次会触发追派；拦下来断言，别真去开代际和聚合锁。"""
    handler = AsyncMock()
    monkeypatch.setattr(loop_module.crawl_batch_dispatcher_service, "handle_batch_event", handler)
    return handler


@pytest.mark.asyncio
async def test_active_batch_is_never_terminated():
    stat = {"total": 2, "success": 1, "failed": 0, "cancelled": 0, "active": 1}
    batch = _batch(age_seconds=3600)

    await CrawlBatchStatusLoop()._reconcile_batch(batch, stat)

    assert _BatchModel.updates == []
    assert batch.status == BatchStatus.RUNNING.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stat",
    [
        None,
        {"total": 1, "success": 1, "failed": 0, "cancelled": 0, "active": 0},
    ],
)
async def test_empty_or_incomplete_batch_times_out_to_failed(stat):
    batch = _batch(age_seconds=3600)

    await CrawlBatchStatusLoop()._reconcile_batch(batch, stat)

    assert _BatchModel.updates[0]["status"] == BatchStatus.FAILED.value
    assert batch.status == BatchStatus.FAILED.value


@pytest.mark.asyncio
async def test_recent_incomplete_batch_waits_for_remaining_dispatches():
    stat = {"total": 1, "success": 1, "failed": 0, "cancelled": 0, "active": 0}
    batch = _batch(age_seconds=30)

    await CrawlBatchStatusLoop()._reconcile_batch(batch, stat)

    assert _BatchModel.updates == []
    assert batch.status == BatchStatus.RUNNING.value


@pytest.mark.asyncio
async def test_undispatched_seeds_are_chased_while_other_seeds_are_still_running(dispatch_events):
    """并发额度压着没派完的 seed，必须在 active>0 时就继续追派。

    等 active 归零再派 = 一个慢 seed 把整批堵到 30 分钟派发超时判 FAILED。
    """
    stat = {"total": 1, "success": 0, "failed": 0, "cancelled": 0, "active": 1}
    batch = _batch(age_seconds=30)

    await CrawlBatchStatusLoop()._reconcile_batch(batch, stat)

    dispatch_events.assert_awaited_once_with("batch_resumed", "batch-1")
    assert _BatchModel.updates == []


@pytest.mark.asyncio
async def test_fully_dispatched_batch_is_not_chased(dispatch_events):
    stat = {"total": 2, "success": 1, "failed": 0, "cancelled": 0, "active": 1}
    batch = _batch(age_seconds=30)

    await CrawlBatchStatusLoop()._reconcile_batch(batch, stat)

    dispatch_events.assert_not_awaited()


@pytest.mark.asyncio
async def test_chase_failure_never_blocks_the_dispatch_timeout_backstop(dispatch_events):
    """追派抛错时状态推导必须继续走，否则"永远派不完"的兜底也被一起废掉。"""
    dispatch_events.side_effect = RuntimeError("no active master epoch")
    stat = {"total": 1, "success": 1, "failed": 0, "cancelled": 0, "active": 0}
    batch = _batch(age_seconds=3600)

    await CrawlBatchStatusLoop()._reconcile_batch(batch, stat)

    assert _BatchModel.updates[0]["status"] == BatchStatus.FAILED.value
