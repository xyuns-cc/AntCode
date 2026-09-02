"""批次 run 的超时判据必须是派发时给出的预算，不是 reconcile 的兜底阈值。

批次 run 没有 Task 行（``TASK_ID_ABSENT``），``_load_task_timeouts`` 查不到它。
落到 300s 兜底就会在 Worker 仍在抓取时把 run 判 TIMEOUT——又一次"控制面结算
抢在真实结果之前"。
"""

from datetime import UTC, datetime, timedelta

from antcode_core.application.services.crawl.batch_dispatcher_service import (
    DEFAULT_CRAWL_TASK_TIMEOUT_SECONDS,
)
from antcode_core.domain.models.task_run import TASK_ID_ABSENT, TaskRun
from antcode_master.control.reconcile_loop import ReconcileLoop, _expired_runs

FALLBACK_THRESHOLD = ReconcileLoop().timeout_threshold
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _run(task_id: int, *, running_for: timedelta) -> TaskRun:
    return TaskRun(task_id=task_id, run_id=f"run-{task_id}", start_time=NOW - running_for)


def _expired_run_ids(runs: list[TaskRun], timeout_map: dict[int, int]) -> list[str]:
    expired = _expired_runs(runs, timeout_map, fallback_threshold=FALLBACK_THRESHOLD, now=NOW)
    return [run.run_id for run in expired]


def test_batch_run_survives_beyond_the_reconcile_fallback():
    """跑了 10 分钟的爬取 run 仍在预算内，不能被判超时。"""
    run = _run(TASK_ID_ABSENT, running_for=timedelta(seconds=FALLBACK_THRESHOLD * 2))

    assert _expired_run_ids([run], {}) == []


def test_batch_run_expires_at_its_dispatched_budget():
    """反向判据：超过派发预算仍必须被回收，不是永不超时。"""
    run = _run(TASK_ID_ABSENT, running_for=timedelta(seconds=DEFAULT_CRAWL_TASK_TIMEOUT_SECONDS + 1))

    assert _expired_run_ids([run], {}) == [run.run_id]


def test_task_owned_run_still_uses_its_own_timeout():
    """有 Task 行的 run 判据不变：按 Task.timeout_seconds。"""
    run = _run(9, running_for=timedelta(seconds=61))

    assert _expired_run_ids([run], {9: 60}) == [run.run_id]
    assert _expired_run_ids([run], {9: 120}) == []
