"""Stable pagination coverage for durable retry-intent recovery."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_master.control import retry_db_recovery


@pytest.mark.asyncio
async def test_retry_db_recovery_reads_every_stable_page(monkeypatch):
    base_time = datetime.now(UTC)
    first_page = [
        SimpleNamespace(
            id=index,
            task_id=index,
            run_id=f"run-{index}",
            retry_count=1,
            next_retry_at=base_time + timedelta(seconds=index // 2),
        )
        for index in range(retry_db_recovery.RECOVERY_BATCH_SIZE)
    ]
    last = SimpleNamespace(
        id=retry_db_recovery.RECOVERY_BATCH_SIZE,
        task_id=999,
        run_id="last-run",
        retry_count=2,
        next_retry_at=base_time + timedelta(days=1),
    )
    backend = SimpleNamespace(schedule=AsyncMock())
    load_page = AsyncMock(side_effect=[first_page, [last]])
    monkeypatch.setattr(retry_db_recovery, "load_recovery_page", load_page)

    recovered = await retry_db_recovery.recover_retry_intents(backend)

    assert recovered == retry_db_recovery.RECOVERY_BATCH_SIZE + 1
    assert backend.schedule.await_count == recovered
    calls = load_page.await_args_list
    assert calls[0].args == (None,)
    assert calls[1].args[0] == retry_db_recovery.RecoveryCursor(
        first_page[-1].next_retry_at,
        first_page[-1].id,
    )


class _RecoveryQuery:
    def __init__(self):
        self.ordering = None
        self.fields = None
        self.page_size = None

    def order_by(self, *ordering):
        self.ordering = ordering
        return self

    def only(self, *fields):
        self.fields = fields
        return self

    def limit(self, page_size):
        self.page_size = page_size
        return self

    def __await__(self):
        async def result():
            return []

        return result().__await__()


@pytest.mark.asyncio
async def test_retry_recovery_query_has_stable_composite_order(monkeypatch):
    query = _RecoveryQuery()
    task_filter = MagicMock(return_value=query)
    monkeypatch.setattr(retry_db_recovery.TaskRun, "filter", task_filter)

    await retry_db_recovery.load_recovery_page(None)

    task_filter.assert_called_once_with(next_retry_at__not_isnull=True)
    assert query.ordering == ("next_retry_at", "id")
    assert query.fields == ("id", "task_id", "run_id", "retry_count", "next_retry_at")
    assert query.page_size == retry_db_recovery.RECOVERY_BATCH_SIZE
