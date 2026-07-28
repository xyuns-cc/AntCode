from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_core.application.services.workers.worker_delete_guard import quiesce_worker_for_delete
from antcode_core.domain.models import TaskRun, Worker, WorkerStatus


class _Transaction:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Query:
    def __init__(self, *, first=None, count=0):
        self.first = AsyncMock(return_value=first)
        self.count = AsyncMock(return_value=count)
        self.update = AsyncMock(return_value=1)

    def using_db(self, _connection):
        return self

    def select_for_update(self):
        return self


@pytest.mark.asyncio
async def test_quiesce_locks_worker_before_marking_maintenance() -> None:
    worker = SimpleNamespace(id=7, name="worker-7", status=WorkerStatus.ONLINE)
    locked_query = _Query(first=worker)
    update_query = _Query()
    runs_query = _Query(count=0)

    with (
        patch(
            "antcode_core.application.services.workers.worker_delete_guard.in_transaction",
            return_value=_Transaction(),
        ),
        patch.object(Worker, "filter", side_effect=[locked_query, update_query]),
        patch.object(TaskRun, "filter", return_value=runs_query),
    ):
        await quiesce_worker_for_delete(worker)

    update_query.update.assert_awaited_once_with(status=WorkerStatus.MAINTENANCE)
    assert worker.status == WorkerStatus.MAINTENANCE


@pytest.mark.asyncio
async def test_quiesce_rejects_active_run_before_status_change() -> None:
    worker = SimpleNamespace(id=7, name="worker-7", status=WorkerStatus.ONLINE)
    locked_query = _Query(first=worker)
    runs_query = _Query(count=1)

    with (
        patch(
            "antcode_core.application.services.workers.worker_delete_guard.in_transaction",
            return_value=_Transaction(),
        ),
        patch.object(Worker, "filter", return_value=locked_query),
        patch.object(TaskRun, "filter", return_value=runs_query),
        pytest.raises(Exception, match="仍有 1 个未终态执行"),
    ):
        await quiesce_worker_for_delete(worker)

    locked_query.update.assert_not_awaited()
    assert worker.status == WorkerStatus.ONLINE
