"""Crawl batch dispatch constraint persistence contracts."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_core.application.services.crawl.batch_dispatcher_service as dispatcher_module
import pytest
from antcode_core.application.services.crawl.batch_dispatch_state import SeedDispatchOutcome
from antcode_core.application.services.crawl.batch_dispatcher_service import (
    CrawlBatchDispatcherService,
)


@pytest.fixture(autouse=True)
def task_run_dispatch_state(monkeypatch):
    monkeypatch.setattr(dispatcher_module.TaskRun, "get_or_none", AsyncMock(return_value=None))
    monkeypatch.setattr(dispatcher_module, "mark_dispatch_succeeded", AsyncMock())
    monkeypatch.setattr(dispatcher_module, "mark_redispatch_enqueued", AsyncMock())


@pytest.mark.asyncio
async def test_failed_batch_dispatch_persists_inferred_constraints(monkeypatch):
    service = CrawlBatchDispatcherService()
    batch = SimpleNamespace(
        public_id="batch-1",
        user_id=1,
        max_pages=100,
        max_retries=2,
        timeout=30,
        request_delay=0.5,
        max_concurrency=5,
        max_depth=2,
    )
    project = SimpleNamespace(public_id="project-1")
    rule = SimpleNamespace(
        to_dispatch_dict=lambda: {"engine": "playwright"},
        region="cn-east",
        require_render=False,
    )
    workers_module = importlib.import_module("antcode_core.application.services.workers")
    redispatch_module = importlib.import_module("antcode_core.application.services.scheduler.redispatch_service")
    dispatch = AsyncMock(return_value=SimpleNamespace(success=False, error="offline"))
    enqueue = AsyncMock(return_value=True)
    monkeypatch.setattr(dispatcher_module.TaskRun, "create", AsyncMock())
    monkeypatch.setattr(workers_module.worker_task_dispatcher, "dispatch_task", dispatch)
    monkeypatch.setattr(redispatch_module.redispatch_service, "enqueue", enqueue)

    # 直派失败 + 入补派队列成功：结果是 REDISPATCH_ENQUEUED，不是"已派发"。
    outcome = await service._dispatch_single_url(batch, project, rule, "https://a.test", seed_count=1)

    assert outcome is SeedDispatchOutcome.REDISPATCH_ENQUEUED
    assert dispatch.await_args.kwargs["region"] == "cn-east"
    assert dispatch.await_args.kwargs["require_render"] is True
    assert enqueue.await_args.kwargs["region"] == "cn-east"
    assert enqueue.await_args.kwargs["require_render"] is True
