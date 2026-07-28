"""Ownership binding rejects terminal TaskRuns under a row lock."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.workers.run_ownership_service import (
    _lock_claimable_run,
)
from antcode_core.domain.models import TaskRun
from antcode_core.domain.models.enums import TaskStatus


def _query(row):
    query = MagicMock()
    query.using_db.return_value = query
    query.select_for_update.return_value = query
    query.first = AsyncMock(return_value=row)
    return query


@pytest.mark.asyncio
async def test_bind_rejects_terminal_taskrun(monkeypatch) -> None:
    row = SimpleNamespace(status=TaskStatus.SUCCESS)
    monkeypatch.setattr(TaskRun, "filter", MagicMock(return_value=_query(row)))

    with pytest.raises(PermissionError, match="已在终态"):
        await _lock_claimable_run("run-1", 42, object())


@pytest.mark.asyncio
async def test_bind_accepts_claimable_taskrun(monkeypatch) -> None:
    row = SimpleNamespace(status=TaskStatus.RUNNING)
    monkeypatch.setattr(TaskRun, "filter", MagicMock(return_value=_query(row)))

    assert await _lock_claimable_run("run-1", 42, object()) is row
