from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers import dispatch_authorization


@pytest.mark.asyncio
async def test_dispatch_rejects_owner_without_worker_use_permission(monkeypatch) -> None:
    monkeypatch.setattr(
        dispatch_authorization,
        "_load_task_run_owners",
        AsyncMock(return_value={"run-1": 7}),
    )
    monkeypatch.setattr(
        dispatch_authorization,
        "_load_worker_access",
        AsyncMock(return_value=(set(), set())),
    )

    with pytest.raises(dispatch_authorization.DispatchWorkerAccessDenied, match="use 权限"):
        await dispatch_authorization.require_task_run_worker_use_access([{"run_id": "run-1"}], 3)


@pytest.mark.asyncio
async def test_dispatch_accepts_admin_or_explicit_use_permission(monkeypatch) -> None:
    monkeypatch.setattr(
        dispatch_authorization,
        "_load_task_run_owners",
        AsyncMock(return_value={"run-admin": 1, "run-user": 7}),
    )
    monkeypatch.setattr(
        dispatch_authorization,
        "_load_worker_access",
        AsyncMock(return_value=({1}, {7})),
    )

    await dispatch_authorization.require_task_run_worker_use_access(
        [{"run_id": "run-admin"}, {"run_id": "run-user"}],
        3,
    )
