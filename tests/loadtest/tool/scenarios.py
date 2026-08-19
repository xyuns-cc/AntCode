from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Sequence
from typing import Any

from .api import AntCodeApi, pace_housekeeping_request
from .config import LoadSettings
from .metrics import LoadReport

FUTURE_SCHEDULE = "2099-01-01T00:00:00+00:00"
SETUP_CONCURRENCY = 10
POLL_INTERVAL_SECONDS = 1.0
# 只要大于 1 就能暴露重复派发；取 5 是为了在报错里看清到底多出了几条。
RUN_HISTORY_PAGE_SIZE = 5
TERMINAL_STATUSES = frozenset({"success", "failed", "cancelled", "timeout", "rejected", "skipped"})
SUCCESS_STATUS = "success"


def require_write_targets(settings: LoadSettings) -> tuple[str, str]:
    if not settings.project_id or not settings.worker_id:
        raise ValueError("write scenarios require load-test project ID and Worker ID")
    return settings.project_id, settings.worker_id


def task_payload(settings: LoadSettings, index: int, prefix: str) -> dict[str, Any]:
    project_id, worker_id = require_write_targets(settings)
    return {
        "name": f"load-{prefix}-{index}-{uuid.uuid4().hex[:10]}",
        "description": "Guarded AntCode load-test task",
        "project_id": project_id,
        "schedule_type": "date",
        "scheduled_time": FUTURE_SCHEDULE,
        "is_active": True,
        "execution_strategy": "specified",
        "specified_worker_id": worker_id,
        "retry_count": 0,
    }


def created_task_ids(report: LoadReport[Any]) -> list[str]:
    task_ids = []
    for sample in report.samples:
        value = sample.value
        if not isinstance(value, dict):
            continue
        data = value.get("data")
        public_id = data.get("id") if isinstance(data, dict) else None
        if isinstance(public_id, str) and public_id:
            task_ids.append(public_id)
    return task_ids


async def create_tasks(
    api: AntCodeApi,
    settings: LoadSettings,
    count: int,
    *,
    prefix: str,
) -> list[str]:
    semaphore = asyncio.Semaphore(min(SETUP_CONCURRENCY, settings.stage.vus))

    async def create(index: int) -> str:
        async with semaphore:
            return await api.create_task(task_payload(settings, index, prefix), index)

    results = await asyncio.gather(*(create(index) for index in range(count)), return_exceptions=True)
    task_ids = [result for result in results if isinstance(result, str)]
    failures = [result for result in results if isinstance(result, BaseException)]
    if not failures:
        return task_ids
    try:
        await api.delete_tasks(task_ids)
    except Exception as cleanup_error:
        raise BaseExceptionGroup("task setup and cleanup both failed", [*failures, cleanup_error]) from cleanup_error
    raise BaseExceptionGroup("task setup failed; created tasks were cleaned up", failures)


async def wait_for_successful_runs(
    api: AntCodeApi,
    task_ids: Sequence[str],
    timeout_seconds: float,
    *,
    expected_worker_id: str,
) -> float:
    started = time.perf_counter()
    deadline = started + timeout_seconds
    pending = set(task_ids)
    while pending and time.perf_counter() < deadline:
        runs = await _read_latest_runs(api, tuple(pending))
        terminal = {task_id: run for task_id, run in runs.items() if run.get("status") in TERMINAL_STATUSES}
        non_success = {
            task_id: run.get("status") for task_id, run in terminal.items() if run.get("status") != SUCCESS_STATUS
        }
        if non_success:
            raise AssertionError(f"backlog contains unsuccessful terminal runs: {non_success}")
        wrong_workers = {
            task_id: run.get("worker_id")
            for task_id, run in terminal.items()
            if run.get("worker_id") != expected_worker_id
        }
        if wrong_workers:
            raise AssertionError(f"successful runs did not execute on Worker {expected_worker_id!r}: {wrong_workers}")
        pending.difference_update(terminal)
        if pending:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    if pending:
        raise AssertionError(f"backlog did not complete before deadline: {sorted(pending)}")
    return time.perf_counter() - started


async def _read_latest_runs(api: AntCodeApi, task_ids: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """读取每个 task 的 run 历史页，先判重复派发，再取最新一条。

    只读 ``size=1`` 会让"同一个 task 被派发成两个 run 执行两次"完全不可见——
    最新那条照样 success，断言照样通过。调度平台的核心正确性正是这一条，
    因此这里读一整页并对 ``retry_count=0`` 的负载任务要求"恰好一个 run"。
    """
    runs_by_task: dict[str, list[dict[str, Any]]] = {}
    for index, task_id in enumerate(task_ids):
        await pace_housekeeping_request(index)
        runs_by_task[task_id] = await api.task_runs(task_id, index, size=RUN_HISTORY_PAGE_SIZE)
    reject_duplicate_runs(runs_by_task)
    return {
        task_id: runs[0] for task_id, runs in runs_by_task.items() if runs and isinstance(runs[0].get("status"), str)
    }


def reject_duplicate_runs(runs_by_task: dict[str, list[dict[str, Any]]]) -> None:
    """负载任务都以 ``retry_count=0`` 创建，多于一个 run 只能来自重复派发。"""
    duplicated = {task_id: len(runs) for task_id, runs in runs_by_task.items() if len(runs) > 1}
    if duplicated:
        raise AssertionError(f"tasks were dispatched more than once (duplicate execution): {duplicated}")


def worker_list_value(value: Any) -> tuple[int, tuple[str, ...]]:
    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        raise ValueError("Worker list response has no data object")
    data = value["data"]
    items = data.get("items")
    total = data.get("total")
    if not isinstance(items, list) or not isinstance(total, int):
        raise ValueError("Worker list response is malformed")
    ids = tuple(
        item["id"] for item in items if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    )
    if len(ids) != len(items) or len(ids) != len(set(ids)):
        raise ValueError("Worker list contains missing or duplicate public IDs")
    if total < len(ids):
        raise ValueError("Worker list total is smaller than the returned page")
    return total, ids


def worker_state(value: Any) -> tuple[str, str, str]:
    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        raise ValueError("Worker detail response has no data object")
    data = value["data"]
    worker_id, status, heartbeat = data.get("id"), data.get("status"), data.get("lastHeartbeat")
    if not all(isinstance(item, str) for item in (worker_id, status, heartbeat)):
        raise ValueError("Worker detail response is malformed")
    return worker_id, status, heartbeat


def assert_churn(states: Sequence[tuple[str, str, str]], worker_ids: Sequence[str]) -> None:
    for worker_id in worker_ids:
        observed = [status.lower() for current_id, status, _ in states if current_id == worker_id]
        compressed = [status for index, status in enumerate(observed) if index == 0 or status != observed[index - 1]]
        if not _contains_ordered(compressed, ("online", "offline", "online")):
            raise AssertionError(f"Worker {worker_id} did not transition online/offline/online: {compressed}")


def _contains_ordered(values: Sequence[str], expected: Sequence[str]) -> bool:
    position = 0
    for value in values:
        if value == expected[position]:
            position += 1
            if position == len(expected):
                return True
    return False
