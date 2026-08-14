"""Scheduler concurrency accounting."""

import importlib
from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models.enums import ProjectType, TaskStatus


class _FakeTaskQuery:
    def using_db(self, _connection):
        return self

    def select_for_update(self):
        return self

    async def first(self):
        return _RUNNING_TASK

    async def update(self, **_kwargs):
        return 1


class _FakeTaskModel:
    @staticmethod
    def filter(**_kwargs):
        return _FakeTaskQuery()


class _Transaction(AbstractAsyncContextManager):
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


_RUNNING_TASK = SimpleNamespace(
    id=1,
    name="running-task",
    status=TaskStatus.RUNNING,
    is_active=True,
)


async def _running_task(_task_id):
    return {
        "task": _RUNNING_TASK,
        "project": SimpleNamespace(type=ProjectType.CODE),
        "project_detail": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_path",
    # core 的 SchedulerService 已无执行链路，唯一的执行权威是 Master。
    ["antcode_master.control.scheduler_loop"],
)
async def test_duplicate_trigger_releases_concurrency_once(monkeypatch, module_path):
    module = importlib.import_module(module_path)
    service = module.SchedulerService()

    monkeypatch.setattr(module.relation_service, "get_task_with_project", _running_task)
    monkeypatch.setattr(module, "Task", _FakeTaskModel)
    monkeypatch.setattr(service, "_get_next_run_time", lambda _task_id: None)
    if hasattr(service, "_count_active_runs"):
        monkeypatch.setattr(service, "_count_active_runs", AsyncMock(return_value=1))
    if hasattr(module, "ensure_leader"):
        monkeypatch.setattr(module, "ensure_leader", AsyncMock(return_value=True))
        monkeypatch.setattr(module, "in_transaction", lambda _name: _Transaction())
    execute_kwargs = {}
    if hasattr(module, "require_scheduler_authority"):
        monkeypatch.setattr(module, "require_scheduler_authority", AsyncMock())
        execute_kwargs["scheduler_fencing_token"] = 7

    await service._execute_task_internal("task-1", **execute_kwargs)

    if hasattr(service, "task_execution_stats"):
        assert service.task_execution_stats["total_executed"] == 1
        assert service.task_execution_stats["currently_running"] == 0
    else:
        assert not hasattr(service, "task_execution_stats")
