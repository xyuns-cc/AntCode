"""``test_dispatch_epoch_wiring`` 的被测环境搭建，不含断言。

观测点统一放在派发边界：假的 ``dispatch_task`` / ``dispatch_batch`` /
``submit_rule_task`` 里调用真实的 ``require_scheduler_dispatch_epoch()``，
它取不到代际就会抛错，因此"记录到的代际"即"派发时真实生效的代际"。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from antcode_core.application.services.crawl import batch_dispatcher_service as crawl_module
from antcode_core.application.services.lease_fenced_ready_publish import (
    require_scheduler_dispatch_epoch,
)
from antcode_web_api.routes.v1 import workers

from tests.unit.conftest import ACTIVE_SCHEDULER_EPOCH

# Master 自己的 Leader 代际，故意与表里的权威代际取不同值：补派若回读了 DB，
# 记录到的就会是 ACTIVE_SCHEDULER_EPOCH 而不是这个值。
LEADER_EPOCH = ACTIVE_SCHEDULER_EPOCH + 100
TASK_ID = 7
PROJECT_ROW_ID = 11
OWNER_ID = 3


class EpochRecorder:
    """在派发边界记录真实生效的 Master 代际。"""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.epochs: list[int] = []

    async def __call__(self, **_kwargs: Any) -> Any:
        self.epochs.append(require_scheduler_dispatch_epoch())
        return self.result


def patch_dispatch_task(monkeypatch, result: Any) -> EpochRecorder:
    services_module = importlib.import_module("antcode_core.application.services.workers")
    recorder = EpochRecorder(result)
    monkeypatch.setattr(services_module.worker_task_dispatcher, "dispatch_task", recorder)
    return recorder


# ---------------------------------------------------------------------------
# Web API 手动派发
# ---------------------------------------------------------------------------


def patch_single_dispatch_deps(monkeypatch) -> None:
    project_module = importlib.import_module("antcode_core.application.services.projects.project_service")
    project = SimpleNamespace(id=PROJECT_ROW_ID, env_location=None, worker_env_name=None)
    task_query = SimpleNamespace(first=AsyncMock(return_value=SimpleNamespace(id=TASK_ID)))
    monkeypatch.setattr(workers, "_resolve_dispatch_worker", AsyncMock(return_value="worker-1"))
    monkeypatch.setattr(project_module.project_service, "get_project_by_id", AsyncMock(return_value=project))
    monkeypatch.setattr(workers.Task, "filter", lambda **_filters: task_query)
    monkeypatch.setattr(workers.TaskRun, "create", AsyncMock(return_value=SimpleNamespace(save=AsyncMock())))
    monkeypatch.setattr(
        workers.TaskRun,
        "filter",
        lambda **_filters: SimpleNamespace(update=AsyncMock(return_value=1)),
    )


def single_request() -> Any:
    return workers.WorkerDispatchTaskRequest(project_id="project-1", task_id=TASK_ID)


def patch_batch_dispatch_deps(monkeypatch, recorder: EpochRecorder) -> None:
    services_module = importlib.import_module("antcode_core.application.services.workers")
    guard_module = importlib.import_module("antcode_web_api.routes.v1.worker_dispatch_guard")
    monkeypatch.setattr(workers, "_resolve_dispatch_worker", AsyncMock(return_value="worker-1"))
    monkeypatch.setattr(
        guard_module,
        "authorize_batch_dispatch_tasks",
        AsyncMock(return_value=[{"project_id": "project-1", "task_id": TASK_ID, "run_id": "run-1"}]),
    )
    monkeypatch.setattr(services_module.worker_task_dispatcher, "dispatch_batch", recorder)


def batch_request() -> Any:
    return workers.WorkerDispatchBatchRequest(
        tasks=[{"project_id": "project-1", "task_id": TASK_ID, "run_id": "run-1"}]
    )


# ---------------------------------------------------------------------------
# 爬虫批次派发 / Master 补派
# ---------------------------------------------------------------------------


def patch_crawl_batch(monkeypatch) -> crawl_module.CrawlBatchDispatcherService:
    service = crawl_module.CrawlBatchDispatcherService()
    batch = SimpleNamespace(
        public_id="batch-1",
        project_id=PROJECT_ROW_ID,
        user_id=OWNER_ID,
        status="running",
        seed_urls=["https://a.test"],
        max_pages=10,
        max_retries=1,
        timeout=30,
        request_delay=0,
        max_concurrency=2,
        max_depth=1,
    )
    project = SimpleNamespace(id=PROJECT_ROW_ID, public_id="project-1", type=crawl_module.ProjectType.RULE)
    rule = SimpleNamespace(to_dispatch_dict=lambda: {}, region=None, require_render=False)
    monkeypatch.setattr(crawl_module.CrawlBatch, "get_or_none", AsyncMock(return_value=batch))
    monkeypatch.setattr(crawl_module.Project, "get_or_none", AsyncMock(return_value=project))
    monkeypatch.setattr(crawl_module.ProjectRule, "get_or_none", AsyncMock(return_value=rule))
    monkeypatch.setattr(crawl_module.TaskRun, "get_or_none", AsyncMock(return_value=None))
    monkeypatch.setattr(crawl_module.TaskRun, "create", AsyncMock())
    monkeypatch.setattr(crawl_module, "mark_dispatch_succeeded", AsyncMock())
    monkeypatch.setattr(service, "_already_dispatched_urls", AsyncMock(return_value=set()))
    return service


def patch_leader_epoch(monkeypatch, token: int | None = None, error: Exception | None = None) -> None:
    loop_module = importlib.import_module("antcode_master.control.redispatch_loop")
    stub = AsyncMock(side_effect=error) if error is not None else AsyncMock(return_value=token)
    monkeypatch.setattr(loop_module, "require_fencing_token", stub)


def redispatch_payload() -> dict[str, Any]:
    return {"run_id": "run-1", "project_id": "project-1", "params": {}}
