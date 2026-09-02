"""``repair_stale_task_status`` 的候选查询必须在栅栏事务之外。

它曾是 reconcile 里唯一「无候选也开栅栏事务」的修复步骤：新任期 epoch
还没落 PG 时，空库每轮都从这里抛 SchedulerAuthorityNotActivated，把后面的
global stream 裁剪与取消收敛一并跳掉。
"""

import importlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models.enums import TaskStatus

repairs_module = importlib.import_module("antcode_master.control.reconcile_repairs")
AUTHORITY_TOKEN = 11
TERMINAL_RUN_AGE_SECONDS = 3600


class _BusyTaskQuery:
    def __init__(self, rows):
        self._rows = rows

    def only(self, *_fields):
        return self

    async def all(self):
        return self._rows


class _TaskUpdate:
    def __init__(self, sink, criteria):
        self._sink = sink
        self._criteria = criteria

    async def update(self, **values):
        self._sink.append((self._criteria, values))
        return 1


class _Tasks:
    rows: list = []
    updates: list = []

    @classmethod
    def filter(cls, **criteria):
        if "status__in" in criteria:
            return _BusyTaskQuery(cls.rows)
        return _TaskUpdate(cls.updates, criteria)


class _LatestRuns:
    latest = None

    @classmethod
    def filter(cls, **_criteria):
        return cls

    @classmethod
    def order_by(cls, *_fields):
        return cls

    @classmethod
    def only(cls, *_fields):
        return cls

    @classmethod
    async def first(cls):
        return cls.latest


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    _Tasks.rows = []
    _Tasks.updates = []
    _LatestRuns.latest = None
    monkeypatch.setattr(repairs_module, "Task", _Tasks)
    monkeypatch.setattr(repairs_module, "TaskRun", _LatestRuns)


@pytest.mark.asyncio
async def test_no_busy_task_skips_the_authority_fence(monkeypatch):
    fence = AsyncMock()
    monkeypatch.setattr(repairs_module, "execute_with_scheduler_authority", fence)

    await repairs_module.repair_stale_task_status(AUTHORITY_TOKEN)

    fence.assert_not_awaited()


@pytest.mark.asyncio
async def test_busy_task_enters_the_fence_and_converges_through_status_cas(monkeypatch):
    """反向对照：没有这条，上面的 assert_not_awaited 在实现整体失效时也照样通过。"""
    _Tasks.rows = [SimpleNamespace(id=7, status=TaskStatus.RUNNING)]
    _LatestRuns.latest = SimpleNamespace(
        id=3,
        status=TaskStatus.SUCCESS,
        created_at=datetime.now(UTC) - timedelta(seconds=TERMINAL_RUN_AGE_SECONDS),
    )
    entered = []

    async def run_with_authority(token, function, **kwargs):
        entered.append(token)
        return await function(**kwargs)

    monkeypatch.setattr(repairs_module, "execute_with_scheduler_authority", run_with_authority)

    await repairs_module.repair_stale_task_status(AUTHORITY_TOKEN)

    assert entered == [AUTHORITY_TOKEN]
    assert _Tasks.updates == [({"id": 7, "status": TaskStatus.RUNNING}, {"status": TaskStatus.SUCCESS})]
