from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.scheduler import cancel_request_service as module


class _Query:
    def __init__(self, *, updated: int = 0, exists: bool = False) -> None:
        self.update = AsyncMock(return_value=updated)
        self.exists = AsyncMock(return_value=exists)


@pytest.mark.asyncio
async def test_record_cancel_request_persists_first_request(monkeypatch) -> None:
    timestamp = datetime(2026, 7, 30, tzinfo=UTC)
    query = _Query(updated=1)
    task_filter = MagicMock(return_value=query)
    monkeypatch.setattr(module.TaskRun, "filter", task_filter)

    recorded = await module.record_cancel_request(
        "run-1",
        requested_by=7,
        requested_at=timestamp,
    )

    assert recorded is True
    query.update.assert_awaited_once_with(
        cancel_requested_at=timestamp,
        cancel_requested_by=7,
    )
    assert task_filter.call_args.kwargs["cancel_requested_at__isnull"] is True


@pytest.mark.asyncio
async def test_record_cancel_request_is_idempotent(monkeypatch) -> None:
    first = _Query(updated=0)
    existing = _Query(exists=True)
    monkeypatch.setattr(module.TaskRun, "filter", MagicMock(side_effect=[first, existing]))

    recorded = await module.record_cancel_request("run-1", requested_by=7)

    assert recorded is True
    existing.exists.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_cancel_request_rejects_terminal_or_missing_run(monkeypatch) -> None:
    first = _Query(updated=0)
    existing = _Query(exists=False)
    monkeypatch.setattr(module.TaskRun, "filter", MagicMock(side_effect=[first, existing]))

    assert await module.record_cancel_request("run-1", requested_by=None) is False
