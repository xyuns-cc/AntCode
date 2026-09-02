"""GET /tasks/running 必须给出前端能 join 的 Worker 标识 (public_id)。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_web_api.routes.v1 import tasks_query

WORKER_INTERNAL_ID = 4242
WORKER_PUBLIC_ID = "wk-7f3a9c21"
OTHER_INTERNAL_ID = 4243
OTHER_PUBLIC_ID = "wk-0d55be10"
EXPECTED_ITEMS = 2


class _RunQuery:
    def __init__(self, runs):
        self._runs = runs

    def filter(self, **_kwargs):
        return self

    def order_by(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def __await__(self):
        async def _resolve():
            return self._runs

        return _resolve().__await__()


def _run(run_id: str, worker_id: int | None):
    return SimpleNamespace(
        task_id=7,
        run_id=run_id,
        status="running",
        start_time=datetime(2026, 9, 1, tzinfo=UTC),
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        worker_id=worker_id,
        retry_count=0,
    )


def _install(monkeypatch, runs, worker_info):
    batch = AsyncMock(return_value=worker_info)
    monkeypatch.setattr(tasks_query, "TaskRun", SimpleNamespace(filter=lambda **_kw: _RunQuery(runs)))
    monkeypatch.setattr(tasks_query, "_running_task_scope", AsyncMock(return_value=None))
    monkeypatch.setattr(tasks_query, "_running_task_map", AsyncMock(return_value={}))
    monkeypatch.setattr(tasks_query.QueryHelper, "batch_get_worker_info", batch)
    return batch


async def _call():
    return await tasks_query.get_running_tasks(
        offset=0,
        limit=100,
        current_user=SimpleNamespace(user_id=1, is_admin=True),
        running_task_hard_cap=200,
    )


@pytest.mark.asyncio
async def test_running_task_worker_id_is_public_id_not_internal_row_id(monkeypatch) -> None:
    _install(
        monkeypatch,
        [_run("run-1", WORKER_INTERNAL_ID)],
        {WORKER_INTERNAL_ID: {"public_id": WORKER_PUBLIC_ID, "name": "worker-a"}},
    )

    item = (await _call()).data[0]

    assert item["worker_id"] == WORKER_PUBLIC_ID
    # 反判据: 绝不能再吐 TaskRun.worker_id 那个内部自增 ID,前端 join 不上。
    assert item["worker_id"] != WORKER_INTERNAL_ID


@pytest.mark.asyncio
async def test_running_tasks_resolve_workers_in_one_batch(monkeypatch) -> None:
    batch = _install(
        monkeypatch,
        [_run("run-1", WORKER_INTERNAL_ID), _run("run-2", OTHER_INTERNAL_ID)],
        {
            WORKER_INTERNAL_ID: {"public_id": WORKER_PUBLIC_ID, "name": "worker-a"},
            OTHER_INTERNAL_ID: {"public_id": OTHER_PUBLIC_ID, "name": "worker-b"},
        },
    )

    items = (await _call()).data

    assert len(items) == EXPECTED_ITEMS
    assert [item["worker_id"] for item in items] == [WORKER_PUBLIC_ID, OTHER_PUBLIC_ID]
    assert batch.await_count == 1
    assert sorted(batch.await_args.args[0]) == [WORKER_INTERNAL_ID, OTHER_INTERNAL_ID]


@pytest.mark.asyncio
async def test_unassigned_run_reports_null_worker(monkeypatch) -> None:
    batch = _install(monkeypatch, [_run("run-1", None)], {})

    item = (await _call()).data[0]

    assert item["worker_id"] is None
    assert batch.await_args.args[0] == []
