import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from antcode_core.domain.models.enums import ScheduleType, TaskStatus
from antcode_core.domain.schemas.task import TaskUpdateRequest

scheduler_module = importlib.import_module("antcode_core.application.services.scheduler.scheduler_service")
TASK_ID = 6


class TransactionProbe:
    def __init__(self) -> None:
        self.connection = object()
        self.active = False

    async def __aenter__(self):
        self.active = True
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.active = False


class LockedTaskQuery:
    def __init__(self, task) -> None:
        self.task = task
        self.connection = None
        self.locked = False

    def using_db(self, connection):
        self.connection = connection
        return self

    def select_for_update(self):
        self.locked = True
        return self

    async def first(self):
        return self.task


def _configure_locked_task(monkeypatch, task):
    transaction = TransactionProbe()
    query = LockedTaskQuery(task)
    filters = []
    monkeypatch.setattr(scheduler_module, "in_transaction", lambda name: transaction)

    def filter_task(cls, **kwargs):
        filters.append(kwargs)
        return query

    monkeypatch.setattr(scheduler_module.Task, "filter", classmethod(filter_task))
    return transaction, query, filters


def _configure_service(monkeypatch, *, publish: AsyncMock | None = None):
    """控制面服务只有 outbox 一条出口，默认把它挡掉以隔离被测语义。"""
    service = scheduler_module.scheduler_service
    monkeypatch.setattr(service, "_publish_event", publish if publish is not None else AsyncMock())
    return service


def _configure_projection(monkeypatch, service):
    """提交后 update_task 回读带投影的任务；本文件只测写入语义，回读用哨兵替身。

    回读本身的真实契约（必须带 project_public_id）由
    tests/unit/core/test_task_update_response_projection.py 覆盖。
    """
    projected = SimpleNamespace(id=None, name="projected")
    monkeypatch.setattr(service, "get_task_by_id", AsyncMock(return_value=projected))
    return projected


@pytest.mark.asyncio
async def test_patch_locks_fresh_row_and_updates_only_requested_fields(monkeypatch) -> None:
    stale = SimpleNamespace(id=9, status=TaskStatus.PENDING, is_active=True, failure_count=0)
    transaction = TransactionProbe()

    async def save_fresh(**kwargs):
        assert transaction.active is True

    fresh = SimpleNamespace(
        id=9,
        name="before",
        status=TaskStatus.RUNNING,
        is_active=False,
        failure_count=4,
        save=AsyncMock(side_effect=save_fresh),
    )
    transaction, query, filters = _configure_locked_task(monkeypatch, fresh)
    monkeypatch.setattr(
        scheduler_module.QueryHelper,
        "get_by_id_or_public_id",
        AsyncMock(return_value=stale),
    )
    service = _configure_service(monkeypatch)
    projected = _configure_projection(monkeypatch, service)

    result = await service.update_task("task-public", TaskUpdateRequest(name="after"), user_id=7)

    assert result is projected
    service.get_task_by_id.assert_awaited_once_with(9, 7)
    assert (fresh.name, fresh.status, fresh.is_active, fresh.failure_count) == (
        "after",
        TaskStatus.RUNNING,
        False,
        4,
    )
    assert query.locked is True
    assert query.connection is transaction.connection
    assert filters == [{"id": 9}]
    fresh.save.assert_awaited_once_with(using_db=transaction.connection, update_fields=["name"])


@pytest.mark.asyncio
async def test_trigger_validation_uses_locked_row_before_save(monkeypatch) -> None:
    fresh = SimpleNamespace(
        id=3,
        name="task",
        schedule_type=ScheduleType.CRON,
        cron_expression="old",
        interval_seconds=None,
        scheduled_time=None,
        next_run_time=None,
        save=AsyncMock(),
    )
    transaction, _, _ = _configure_locked_task(monkeypatch, fresh)
    service = _configure_service(monkeypatch)
    validate = Mock(side_effect=ValueError("bad trigger"))
    monkeypatch.setattr(service, "_create_trigger", validate)
    monkeypatch.setattr(
        scheduler_module.QueryHelper,
        "get_by_id_or_public_id",
        AsyncMock(return_value=SimpleNamespace(id=3)),
    )

    with pytest.raises(ValueError, match="任务触发器配置非法: bad trigger"):
        await service.update_task(
            "task-public",
            TaskUpdateRequest(cron_expression="*/5 * * * *"),
            user_id=7,
        )

    validate.assert_called_once_with(fresh)
    assert fresh.cron_expression == "*/5 * * * *"
    fresh.save.assert_not_awaited()
    assert transaction.active is False


@pytest.mark.asyncio
async def test_control_plane_outbox_is_written_inside_update_transaction(monkeypatch) -> None:
    transaction = TransactionProbe()

    async def assert_in_transaction(*args, **kwargs):
        assert transaction.active is True
        assert kwargs["connection"] is transaction.connection

    fresh = SimpleNamespace(id=5, name="before", save=AsyncMock())
    transaction, _, _ = _configure_locked_task(monkeypatch, fresh)
    publish = AsyncMock(side_effect=assert_in_transaction)
    service = _configure_service(monkeypatch, publish=publish)
    projected = _configure_projection(monkeypatch, service)
    monkeypatch.setattr(
        scheduler_module.QueryHelper,
        "get_by_id_or_public_id",
        AsyncMock(return_value=SimpleNamespace(id=5)),
    )

    result = await service.update_task("task-public", TaskUpdateRequest(name="after"), user_id=7)

    assert result is projected
    fresh.save.assert_awaited_once_with(using_db=transaction.connection, update_fields=["name"])
    publish.assert_awaited_once_with("task_changed", 5, connection=transaction.connection)


@pytest.mark.asyncio
async def test_is_active_toggle_publishes_inside_the_same_transaction(monkeypatch) -> None:
    """is_active 变更同样只走 outbox：core 侧不再有本地调度器同步分支。"""
    transaction = TransactionProbe()

    async def assert_in_transaction(*_args, **kwargs):
        assert transaction.active is True
        assert kwargs["connection"] is transaction.connection

    fresh = SimpleNamespace(id=TASK_ID, name="task", is_active=True, save=AsyncMock())
    transaction, _, _ = _configure_locked_task(monkeypatch, fresh)
    publish = AsyncMock(side_effect=assert_in_transaction)
    service = _configure_service(monkeypatch, publish=publish)
    projected = _configure_projection(monkeypatch, service)
    monkeypatch.setattr(
        scheduler_module.QueryHelper,
        "get_by_id_or_public_id",
        AsyncMock(return_value=SimpleNamespace(id=TASK_ID)),
    )

    result = await service.update_task("task-public", TaskUpdateRequest(is_active=False), user_id=7)

    assert result is projected
    assert fresh.is_active is False
    fresh.save.assert_awaited_once_with(using_db=transaction.connection, update_fields=["is_active"])
    publish.assert_awaited_once_with("task_changed", TASK_ID, connection=transaction.connection)
