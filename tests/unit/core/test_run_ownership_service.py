from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers import run_ownership_service


@pytest.mark.asyncio
async def test_worker_must_own_every_reported_run(monkeypatch):
    worker = SimpleNamespace(id=7)
    query = SimpleNamespace(values_list=AsyncMock(return_value=[("run-1", 7), ("run-2", 8)]))
    monkeypatch.setattr(run_ownership_service.TaskRun, "filter", lambda **_kwargs: query)

    with pytest.raises(PermissionError, match="不属于"):
        await run_ownership_service.require_worker_owns_runs(worker, {"run-1", "run-2"})


@pytest.mark.asyncio
async def test_missing_run_is_rejected(monkeypatch):
    worker = SimpleNamespace(id=7)
    query = SimpleNamespace(values_list=AsyncMock(return_value=[]))
    monkeypatch.setattr(run_ownership_service.TaskRun, "filter", lambda **_kwargs: query)

    with pytest.raises(PermissionError, match="不存在"):
        await run_ownership_service.require_worker_owns_run(worker, "run-missing")
