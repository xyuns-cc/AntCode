"""Crawl cancellation under aggregate-serialized lifecycle handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.application.services.crawl.batch_dispatcher_service import (
    CrawlBatchDispatcherService,
)

EXPECTED_CANCEL_COUNT = 2


@pytest.mark.asyncio
async def test_no_active_runs_noop() -> None:
    service = CrawlBatchDispatcherService()
    query = AsyncMock(return_value=[])
    cancel = AsyncMock()

    with (
        patch.object(service, "_active_run_ids_for_batch", query),
        patch.object(
            service,
            "_cancel_active_run",
            cancel,
        ),
    ):
        await service._on_batch_cancelled("batch-1")

    query.assert_awaited_once_with("batch-1")
    cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_aggregate_locked_cancel_processes_authoritative_snapshot_once() -> None:
    service = CrawlBatchDispatcherService()
    query = AsyncMock(return_value=["run-1", "run-2"])
    cancel = AsyncMock(return_value=(True, True))

    with (
        patch.object(service, "_active_run_ids_for_batch", query),
        patch.object(
            service,
            "_cancel_active_run",
            cancel,
        ),
    ):
        await service._on_batch_cancelled("batch-1")

    query.assert_awaited_once_with("batch-1")
    assert [call.args[0] for call in cancel.await_args_list] == ["run-1", "run-2"]


@pytest.mark.asyncio
async def test_cancel_failure_is_exposed_after_snapshot() -> None:
    service = CrawlBatchDispatcherService()
    query = AsyncMock(return_value=["run-fail", "run-2"])
    cancel = AsyncMock(side_effect=[(False, False), (True, True)])

    with (
        patch.object(service, "_active_run_ids_for_batch", query),
        patch.object(
            service,
            "_cancel_active_run",
            cancel,
        ),
    ):
        with pytest.raises(RuntimeError, match="run-fail"):
            await service._on_batch_cancelled("batch-1")

    assert cancel.await_count == EXPECTED_CANCEL_COUNT
