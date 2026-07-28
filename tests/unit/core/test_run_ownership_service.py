from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers import run_ownership_service, spider_run_access
from antcode_core.domain.models.enums import TaskStatus


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


@pytest.mark.asyncio
async def test_log_runs_must_match_worker_lease_generation(monkeypatch):
    worker = SimpleNamespace(id=7)
    query = SimpleNamespace(
        values_list=AsyncMock(
            return_value=[
                ("run-1", 7, "lease-current"),
                ("run-2", 7, "lease-old"),
            ]
        )
    )
    monkeypatch.setattr(run_ownership_service.TaskRun, "filter", lambda **_kwargs: query)

    with pytest.raises(PermissionError, match="代际"):
        await run_ownership_service.require_worker_owns_runs_for_lease(
            worker,
            {"run-1", "run-2"},
            lease_id="lease-current",
        )


@pytest.mark.asyncio
async def test_log_runs_accept_matching_worker_lease_generation(monkeypatch):
    worker = SimpleNamespace(id=7)
    query = SimpleNamespace(
        values_list=AsyncMock(return_value=[("run-1", 7, "lease-current")]),
    )
    monkeypatch.setattr(run_ownership_service.TaskRun, "filter", lambda **_kwargs: query)

    await run_ownership_service.require_worker_owns_runs_for_lease(
        worker,
        {"run-1"},
        lease_id="lease-current",
    )


@pytest.mark.asyncio
async def test_spider_project_must_match_run_project(monkeypatch):
    worker = SimpleNamespace(id=7)
    execution = SimpleNamespace(
        id=17,
        worker_id=7,
        task_id=11,
        lease_id="lease-1",
        status=TaskStatus.RUNNING,
    )
    execution_query = SimpleNamespace(first=AsyncMock(return_value=execution))
    task_query = SimpleNamespace(first=AsyncMock(return_value=SimpleNamespace(project_id=13)))
    project_query = SimpleNamespace(exists=AsyncMock(return_value=False))
    monkeypatch.setattr(run_ownership_service.TaskRun, "filter", lambda **_kwargs: execution_query)
    monkeypatch.setattr(spider_run_access.Task, "filter", lambda **_kwargs: task_query)
    monkeypatch.setattr(spider_run_access.Project, "filter", lambda **_kwargs: project_query)

    with pytest.raises(PermissionError, match="project_id"):
        await run_ownership_service.require_worker_owns_spider_run(
            worker,
            "run-1",
            "foreign-project",
            lease_id="lease-1",
        )


@pytest.mark.asyncio
async def test_terminal_spider_run_rejects_further_writes(monkeypatch):
    worker = SimpleNamespace(id=7)
    execution = SimpleNamespace(
        id=17,
        worker_id=7,
        task_id=11,
        lease_id="lease-1",
        status=TaskStatus.SUCCESS,
    )
    query = SimpleNamespace(first=AsyncMock(return_value=execution))
    monkeypatch.setattr(run_ownership_service.TaskRun, "filter", lambda **_kwargs: query)

    with pytest.raises(PermissionError, match="终态"):
        await run_ownership_service.require_worker_owns_spider_run(
            worker,
            "run-1",
            "project-1",
            lease_id="lease-1",
        )


@pytest.mark.asyncio
async def test_old_spider_lease_is_rejected(monkeypatch):
    worker = SimpleNamespace(id=7)
    execution = SimpleNamespace(
        id=17,
        worker_id=7,
        task_id=11,
        lease_id="lease-current",
        status=TaskStatus.RUNNING,
    )
    query = SimpleNamespace(first=AsyncMock(return_value=execution))
    monkeypatch.setattr(run_ownership_service.TaskRun, "filter", lambda **_kwargs: query)

    with pytest.raises(spider_run_access.StaleSpiderLeaseError, match="代际"):
        await run_ownership_service.require_worker_owns_spider_run(
            worker,
            "run-1",
            "project-1",
            lease_id="lease-old",
        )


@pytest.mark.asyncio
async def test_spider_write_binds_unassigned_lease_once(monkeypatch):
    worker = SimpleNamespace(id=7)
    execution = SimpleNamespace(
        id=17,
        worker_id=7,
        task_id=11,
        lease_id=None,
        status=TaskStatus.RUNNING,
    )
    execution_query = SimpleNamespace(first=AsyncMock(return_value=execution))
    update_query = SimpleNamespace(update=AsyncMock(return_value=1))

    def task_run_filter(**kwargs):
        return update_query if "id" in kwargs else execution_query

    task_query = SimpleNamespace(first=AsyncMock(return_value=SimpleNamespace(project_id=13)))
    project_query = SimpleNamespace(exists=AsyncMock(return_value=True))
    monkeypatch.setattr(run_ownership_service.TaskRun, "filter", task_run_filter)
    monkeypatch.setattr(spider_run_access.Task, "filter", lambda **_kwargs: task_query)
    monkeypatch.setattr(spider_run_access.Project, "filter", lambda **_kwargs: project_query)

    await run_ownership_service.require_worker_owns_spider_run(
        worker,
        "run-1",
        "project-1",
        lease_id="lease-1",
    )

    update_query.update.assert_awaited_once_with(lease_id="lease-1")


@pytest.mark.asyncio
async def test_spider_lease_binding_rejects_concurrent_worker_reassignment(monkeypatch):
    worker = SimpleNamespace(id=7)
    execution = SimpleNamespace(
        id=17,
        worker_id=7,
        task_id=11,
        lease_id=None,
        status=TaskStatus.RUNNING,
    )
    execution_query = SimpleNamespace(first=AsyncMock(return_value=execution))
    update_query = SimpleNamespace(update=AsyncMock(return_value=0))
    update_filter: dict = {}

    def task_run_filter(**kwargs):
        if "id" not in kwargs:
            return execution_query
        update_filter.update(kwargs)
        return update_query

    task_query = SimpleNamespace(first=AsyncMock(return_value=SimpleNamespace(project_id=13)))
    project_query = SimpleNamespace(exists=AsyncMock(return_value=True))
    monkeypatch.setattr(run_ownership_service.TaskRun, "filter", task_run_filter)
    monkeypatch.setattr(spider_run_access.Task, "filter", lambda **_kwargs: task_query)
    monkeypatch.setattr(spider_run_access.Project, "filter", lambda **_kwargs: project_query)

    with pytest.raises(PermissionError, match="并发冲突"):
        await run_ownership_service.require_worker_owns_spider_run(
            worker,
            "run-1",
            "project-1",
            lease_id="lease-1",
        )

    assert update_filter["worker_id"] == 7
