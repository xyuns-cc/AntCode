from unittest.mock import AsyncMock

import pytest

from tests.loadtest.tool.api import HOUSEKEEPING_INTERVAL_SECONDS, AntCodeApi
from tests.loadtest.tool.scenarios import _read_latest_runs

DELETED_STATUS = 404
EXPECTED_SLEEP_CALLS = 4
TASK_IDS = ("task-1", "task-2", "task-3")


class _RunApi:
    async def task_runs(self, task_id: str, _index: int = 0, *, size: int = 1) -> list[dict[str, str]]:
        return [{"status": "success", "worker_id": "worker-1", "task_id": task_id}]


class _VerifyApi(AntCodeApi):
    def __init__(self) -> None:
        pass

    async def _task_status(self, _task_id: str) -> int:
        return DELETED_STATUS


@pytest.mark.asyncio
async def test_post_load_housekeeping_requests_are_paced(monkeypatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("tests.loadtest.tool.api.asyncio.sleep", sleep)

    await _read_latest_runs(_RunApi(), TASK_IDS)  # type: ignore[arg-type]
    await _VerifyApi()._verify_tasks_deleted(list(TASK_IDS))

    assert sleep.await_count == EXPECTED_SLEEP_CALLS
    assert all(call.args == (HOUSEKEEPING_INTERVAL_SECONDS,) for call in sleep.await_args_list)
