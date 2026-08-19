"""Guarded task submission, dispatch, and backlog load scenarios."""

from __future__ import annotations

from typing import Any

import pytest

from tests.loadtest.tool.api import AntCodeApi
from tests.loadtest.tool.config import LoadSettings, Stage
from tests.loadtest.tool.metrics import LoadReport, assert_report, emit_report
from tests.loadtest.tool.runner import run_load
from tests.loadtest.tool.scenarios import (
    create_tasks,
    created_task_ids,
    require_write_targets,
    task_payload,
    wait_for_successful_runs,
)

pytestmark = [pytest.mark.loadtest_scenario, pytest.mark.loadtest_write, pytest.mark.asyncio]
HTTP_OK = frozenset({200})
TRIGGER_ACCEPTED = 200
# 并发触发同一个 task 时，去重锁的败者返回 409；两者之外的任何码都是缺陷。
TRIGGER_DEDUP_STATUSES = frozenset({TRIGGER_ACCEPTED, 409})


@pytest.mark.asyncio
async def test_high_concurrency_task_submission(load_settings: LoadSettings) -> None:
    """Submit unique future-scheduled tasks at the configured concurrency."""
    require_write_targets(load_settings)
    task_ids: list[str] = []
    async with AntCodeApi(load_settings) as api:

        async def submit(index: int):
            body = task_payload(load_settings, index, "submission")
            return await api.call("POST", "/tasks", index, json_body=body)

        try:
            report = await run_load("task-submission", load_settings.stage, submit)
            task_ids = created_task_ids(report)
            assert_report(report, load_settings.thresholds, HTTP_OK)
            assert len(task_ids) == report.summary.successful
        finally:
            await api.delete_tasks(task_ids)
    emit_report(report)


@pytest.mark.asyncio
async def test_task_dispatch_throughput(load_settings: LoadSettings) -> None:
    """Trigger each prepared task once, avoiding the production dedup lock."""
    _, worker_id = require_write_targets(load_settings)
    async with AntCodeApi(load_settings) as api:
        task_ids = await create_tasks(api, load_settings, load_settings.stage.request_count, prefix="dispatch")

        async def trigger(index: int):
            return await api.call("POST", f"/tasks/{task_ids[index]}/trigger", index)

        try:
            report = await run_load("task-dispatch", load_settings.stage, trigger)
            all_triggered = report.summary.failed == 0 and _all_triggered(report.values)
            if all_triggered:
                completion_seconds = await wait_for_successful_runs(
                    api,
                    task_ids,
                    load_settings.backlog_timeout_seconds,
                    expected_worker_id=worker_id,
                )
            assert_report(report, load_settings.thresholds, HTTP_OK)
            assert all_triggered
        finally:
            await api.delete_tasks(task_ids)
    emit_report(
        report,
        {
            "worker_completion_seconds": round(completion_seconds, 6),
            "worker_completion_tasks_per_second": round(len(task_ids) / completion_seconds, 6),
        },
    )


@pytest.mark.asyncio
async def test_backlog_recovery(load_settings: LoadSettings) -> None:
    """Create more work than one Worker can execute and require full recovery."""
    _, worker_id = require_write_targets(load_settings)
    async with AntCodeApi(load_settings) as api:
        worker = await api.require_json("GET", f"/workers/{worker_id}")
        concurrency = _worker_concurrency(worker)
        task_count = max(load_settings.stage.request_count, concurrency + 1)
        stage = _stage_for_count(load_settings.stage, task_count)
        task_ids = await create_tasks(api, load_settings, task_count, prefix="backlog")

        async def trigger(index: int):
            return await api.call("POST", f"/tasks/{task_ids[index]}/trigger", index)

        try:
            report = await run_load("backlog-trigger", stage, trigger)
            all_triggered = report.summary.failed == 0 and _all_triggered(report.values)
            if all_triggered:
                recovery_seconds = await wait_for_successful_runs(
                    api,
                    task_ids,
                    load_settings.backlog_timeout_seconds,
                    expected_worker_id=worker_id,
                )
            assert_report(report, load_settings.thresholds, HTTP_OK)
            assert all_triggered
        finally:
            await api.delete_tasks(task_ids)
    emit_report(
        report,
        {
            "backlog_recovery_seconds": round(recovery_seconds, 6),
            "backlog_recovery_tasks_per_second": round(task_count / recovery_seconds, 6),
        },
    )


@pytest.mark.asyncio
async def test_concurrent_trigger_deduplication(load_settings: LoadSettings) -> None:
    """Aim every virtual user at one task and require a single deduplicated run.

    ``task-dispatch`` 刻意一 task 一次触发以绕开去重锁，于是生产里最常见的
    并发形态——同一个任务被同时触发多次——在整套压测里从没被打过。
    """
    _, worker_id = require_write_targets(load_settings)
    async with AntCodeApi(load_settings) as api:
        task_ids = await create_tasks(api, load_settings, 1, prefix="dedup")

        async def trigger(index: int):
            return await api.call("POST", f"/tasks/{task_ids[0]}/trigger", index)

        try:
            report = await run_load("trigger-dedup", load_settings.stage, trigger)
            observed = {int(code) for code, _ in report.summary.status_codes if code != "network_error"}
            run_ids = _accepted_run_ids(report)
            assert report.summary.server_errors_5xx == 0, report.summary
            assert observed <= TRIGGER_DEDUP_STATUSES, report.summary
            assert len(run_ids) == 1, run_ids
            completion_seconds = await wait_for_successful_runs(
                api,
                task_ids,
                load_settings.backlog_timeout_seconds,
                expected_worker_id=worker_id,
            )
        finally:
            await api.delete_tasks(task_ids)
    emit_report(report, {"deduplicated_run_id": run_ids.pop(), "completion_seconds": round(completion_seconds, 6)})


def _accepted_run_ids(report: LoadReport[Any]) -> set[str]:
    """被接受的触发返回的 run 身份；去重成立时它们必须全部相同。"""
    run_ids: set[str] = set()
    for sample in report.samples:
        data = sample.value.get("data") if isinstance(sample.value, dict) else None
        run_id = data.get("run_id") if isinstance(data, dict) else None
        if sample.status_code == TRIGGER_ACCEPTED and isinstance(run_id, str) and run_id:
            run_ids.add(run_id)
    return run_ids


def _all_triggered(values: tuple[Any, ...]) -> bool:
    for value in values:
        data = value.get("data") if isinstance(value, dict) else None
        if not isinstance(data, dict) or data.get("triggered") is not True:
            return False
    return True


def _worker_concurrency(payload: dict[str, Any]) -> int:
    data = payload.get("data")
    metrics = data.get("metrics") if isinstance(data, dict) else None
    concurrency = metrics.get("maxConcurrentTasks") if isinstance(metrics, dict) else None
    if not isinstance(concurrency, int) or concurrency <= 0:
        raise ValueError("Worker response has no positive maxConcurrentTasks")
    return concurrency


def _stage_for_count(stage: Stage, count: int) -> Stage:
    return Stage(stage.vus, stage.qps, count / stage.qps)
