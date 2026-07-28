"""Database-filtered Crawl batch dispatcher queries."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_core.application.services.crawl.batch_dispatcher_service as dispatcher_module
import pytest
from antcode_core.application.services.crawl.batch_dispatcher_service import (
    DEFAULT_CRAWL_TASK_TIMEOUT_SECONDS,
    CrawlBatchDispatcherService,
)


@pytest.fixture
def connection(monkeypatch):
    value = AsyncMock()
    monkeypatch.setattr(dispatcher_module.Tortoise, "get_connection", lambda _name: value)
    return value


@pytest.mark.asyncio
async def test_dispatched_urls_are_filtered_in_postgres(connection):
    connection.execute_query_dict.return_value = [
        {"seed_url": "https://a.test"},
        {"seed_url": "https://b.test"},
    ]

    result = await CrawlBatchDispatcherService()._already_dispatched_urls("batch-1")

    assert result == {"https://a.test", "https://b.test"}
    query, values = connection.execute_query_dict.await_args.args
    assert "result_data->>'crawl_batch_id' = $1" in query
    assert values == ["batch-1"]


@pytest.mark.asyncio
async def test_active_runs_are_filtered_by_batch_and_status_in_postgres(connection):
    connection.execute_query_dict.return_value = [{"run_id": "run-1"}]

    result = await CrawlBatchDispatcherService()._active_run_ids_for_batch("batch-1")

    assert result == ["run-1"]
    query, values = connection.execute_query_dict.await_args.args
    assert "status IN ($2, $3, $4, $5)" in query
    assert values[0] == "batch-1"
    assert values[1:] == ["pending", "dispatching", "queued", "running"]


@pytest.mark.asyncio
async def test_partial_dispatch_failure_is_exposed(monkeypatch):
    service = CrawlBatchDispatcherService()
    batch = SimpleNamespace(
        public_id="batch-1",
        project_id=1,
        status="running",
        seed_urls=["https://a.test", "https://b.test"],
    )
    project = SimpleNamespace(id=1, public_id="project-1", type="rule")
    rule = SimpleNamespace()
    monkeypatch.setattr(dispatcher_module.CrawlBatch, "get_or_none", AsyncMock(return_value=batch))
    monkeypatch.setattr(dispatcher_module.Project, "get_or_none", AsyncMock(return_value=project))
    monkeypatch.setattr(dispatcher_module.ProjectRule, "get_or_none", AsyncMock(return_value=rule))
    monkeypatch.setattr(service, "_already_dispatched_urls", AsyncMock(return_value=set()))
    monkeypatch.setattr(service, "_dispatch_single_url", AsyncMock(side_effect=[True, False]))

    with pytest.raises(RuntimeError, match="未持久化失败"):
        await service._on_batch_started("batch-1")


@pytest.mark.asyncio
async def test_cancel_keeps_run_active_when_control_fails(monkeypatch):
    service = CrawlBatchDispatcherService()
    status_service = AsyncMock()
    status_module = importlib.import_module("antcode_core.application.services.scheduler.execution_status_service")
    monkeypatch.setattr(service, "_active_run_ids_for_batch", AsyncMock(return_value=["run-1"]))
    monkeypatch.setattr(service, "_send_worker_cancel", AsyncMock(return_value=False))
    monkeypatch.setattr(status_module, "execution_status_service", status_service)

    with pytest.raises(RuntimeError, match="取消未完成"):
        await service._on_batch_cancelled("batch-1")

    status_service.update_runtime_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_maps_batch_limits_without_reusing_request_timeout(monkeypatch):
    service = CrawlBatchDispatcherService()
    batch = SimpleNamespace(
        public_id="batch-1",
        user_id=1,
        max_pages=100,
        max_retries=2,
        timeout=30,
        request_delay=0.5,
        max_concurrency=17,
        max_depth=4,
    )
    project = SimpleNamespace(public_id="project-1")
    rule = SimpleNamespace(to_dispatch_dict=lambda: {"target_url": "https://seed.test"})
    dispatch = AsyncMock(return_value=SimpleNamespace(success=True))
    workers_module = importlib.import_module("antcode_core.application.services.workers")
    monkeypatch.setattr(dispatcher_module.TaskRun, "create", AsyncMock())
    monkeypatch.setattr(workers_module.worker_task_dispatcher, "dispatch_task", dispatch)

    result = await service._dispatch_single_url(batch, project, rule, "https://a.test")

    assert result is True
    kwargs = dispatch.await_args.kwargs
    rule_detail = kwargs["params"]["kwargs"]["rule_detail"]
    assert kwargs["timeout"] == DEFAULT_CRAWL_TASK_TIMEOUT_SECONDS
    assert rule_detail["timeout"] == 30
    assert rule_detail["concurrent_requests"] == 17
    assert rule_detail["max_depth"] == 4


@pytest.mark.asyncio
async def test_dispatch_preserves_zero_retry_and_request_delay(monkeypatch):
    service = CrawlBatchDispatcherService()
    batch = SimpleNamespace(
        public_id="batch-1",
        user_id=1,
        max_pages=100,
        max_retries=0,
        timeout=30,
        request_delay=0,
        max_concurrency=17,
        max_depth=4,
    )
    project = SimpleNamespace(public_id="project-1")
    rule = SimpleNamespace(to_dispatch_dict=lambda: {"target_url": "https://seed.test", "retry_count": 3})
    dispatch = AsyncMock(return_value=SimpleNamespace(success=True))
    workers_module = importlib.import_module("antcode_core.application.services.workers")
    monkeypatch.setattr(dispatcher_module.TaskRun, "create", AsyncMock())
    monkeypatch.setattr(workers_module.worker_task_dispatcher, "dispatch_task", dispatch)

    assert await service._dispatch_single_url(batch, project, rule, "https://a.test") is True

    rule_detail = dispatch.await_args.kwargs["params"]["kwargs"]["rule_detail"]
    assert rule_detail["retry_count"] == 0
    assert rule_detail["request_delay"] == 0


@pytest.mark.asyncio
async def test_invalid_batch_override_fails_before_task_run_creation(monkeypatch):
    service = CrawlBatchDispatcherService()
    batch = SimpleNamespace(
        public_id="batch-1",
        user_id=1,
        max_pages=100,
        max_retries="invalid",
        timeout=30,
        request_delay=0.5,
        max_concurrency=17,
        max_depth=4,
    )
    project = SimpleNamespace(public_id="project-1")
    rule = SimpleNamespace(to_dispatch_dict=lambda: {"target_url": "https://seed.test"})
    create = AsyncMock()
    monkeypatch.setattr(dispatcher_module.TaskRun, "create", create)

    with pytest.raises(ValueError, match="field=max_retries"):
        await service._dispatch_single_url(batch, project, rule, "https://a.test")

    create.assert_not_awaited()
