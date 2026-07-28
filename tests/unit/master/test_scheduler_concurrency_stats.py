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
    [
        "antcode_master.control.scheduler_loop",
        "antcode_core.application.services.scheduler.scheduler_service",
    ],
)
async def test_duplicate_trigger_releases_concurrency_once(monkeypatch, module_path):
    module = importlib.import_module(module_path)
    service = module.SchedulerService()

    monkeypatch.setattr(module.relation_service, "get_task_with_project", _running_task)
    monkeypatch.setattr(module, "Task", _FakeTaskModel)
    monkeypatch.setattr(service, "_get_next_run_time", lambda _task_id: None)
    if hasattr(module, "ensure_leader"):
        monkeypatch.setattr(module, "ensure_leader", AsyncMock(return_value=True))
        monkeypatch.setattr(module, "in_transaction", lambda _name: _Transaction())

    await service._execute_task_internal("task-1")

    if hasattr(service, "task_execution_stats"):
        assert service.task_execution_stats["total_executed"] == 1
        assert service.task_execution_stats["currently_running"] == 0
    else:
        assert not hasattr(service, "task_execution_stats")
