from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from antcode_core.domain.models.enums import BatchStatus
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
