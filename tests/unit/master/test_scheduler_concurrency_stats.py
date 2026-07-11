"""Scheduler concurrency accounting."""

from types import SimpleNamespace

import pytest
from antcode_core.domain.models.enums import ProjectType, TaskStatus


class _FakeTaskQuery:
    async def update(self, **_kwargs):
        return 1


class _FakeTaskModel:
    @staticmethod
    def filter(**_kwargs):
        return _FakeTaskQuery()


async def _running_task(_task_id):
    task = SimpleNamespace(
        id=1,
        name="running-task",
        status=TaskStatus.RUNNING,
        is_active=True,
    )
    return {
        "task": task,
        "project": SimpleNamespace(type=ProjectType.CODE),
        "project_detail": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_path",
    [
        "antcode_master.loops.scheduler_loop",
        "antcode_core.application.services.scheduler.scheduler_service",
    ],
)
async def test_duplicate_trigger_releases_concurrency_once(monkeypatch, module_path):
    module = pytest.importorskip(module_path)
    service = module.SchedulerService()

    monkeypatch.setattr(module.relation_service, "get_task_with_project", _running_task)
    monkeypatch.setattr(module, "Task", _FakeTaskModel)
    monkeypatch.setattr(service, "_get_next_run_time", lambda _task_id: None)

    await service._execute_task_internal("task-1")

    assert not hasattr(service, "task_execution_stats")
