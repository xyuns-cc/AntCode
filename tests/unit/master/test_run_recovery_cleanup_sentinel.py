"""中断 run 的归因必须真实（第五棒 P2，与 10e44b7 同族）。

爬取批次 run 的 ``task_id`` 恒为 ``TASK_ID_ABSENT``，``scheduled_tasks`` 里永远没有
id=0 的行。老 ``_cleanup_orphan_runs`` 只判「task_id 不在 task_map 里」，于是每一个
中断的批次 run 都被写上「任务已被删除」——一句假消息，会把排障的人引去查删除记录。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from antcode_core.domain.models.task_run import TASK_ID_ABSENT
from antcode_master.run_recovery_cleanup import (
    INTERRUPTED_BATCH_ERROR,
    ORPHAN_TASK_ERROR,
    cleanup_unrecoverable_runs,
)

LIVE_TASK_ID = 7
DELETED_TASK_ID = 99


class _FakeTaskRun:
    """记录每一次 bulk update 的过滤条件与写入值。"""

    def __init__(self) -> None:
        self.updates: list[tuple[list[str], str]] = []
        self._pending: list[str] = []

    def filter(self, **kwargs):
        self._pending = list(kwargs["run_id__in"])
        return self

    async def update(self, **kwargs):
        self.updates.append((self._pending, kwargs["error_message"]))

    def error_for(self, run_id: str) -> str | None:
        return next((message for run_ids, message in self.updates if run_id in run_ids), None)


class _Status:
    FAILED = "failed"


def _run(run_id: str, task_id: int) -> SimpleNamespace:
    return SimpleNamespace(run_id=run_id, task_id=task_id)


@pytest.mark.asyncio
async def test_batch_runs_are_not_reported_as_deleted_tasks() -> None:
    task_run = _FakeTaskRun()
    runs = [
        _run("batch-1", TASK_ID_ABSENT),
        _run("batch-2", TASK_ID_ABSENT),
        _run("orphan-1", DELETED_TASK_ID),
        _run("healthy-1", LIVE_TASK_ID),
    ]

    await cleanup_unrecoverable_runs(runs, {LIVE_TASK_ID: object()}, TaskRun=task_run, TaskStatus=_Status)

    # 批次 run 依然要收敛成终态（否则永久卡住并阻塞 Worker 删除），但归因必须真实。
    assert task_run.error_for("batch-1") == INTERRUPTED_BATCH_ERROR
    assert task_run.error_for("batch-2") == INTERRUPTED_BATCH_ERROR
    # 真的丢了 Task 行的才叫「任务已被删除」。
    assert task_run.error_for("orphan-1") == ORPHAN_TASK_ERROR
    # Task 还在的 run 交给恢复流程，这里一个字都不能改。
    assert task_run.error_for("healthy-1") is None


@pytest.mark.asyncio
async def test_no_update_is_issued_when_every_run_is_recoverable() -> None:
    task_run = _FakeTaskRun()

    await cleanup_unrecoverable_runs(
        [_run("healthy-1", LIVE_TASK_ID)],
        {LIVE_TASK_ID: object()},
        TaskRun=task_run,
        TaskStatus=_Status,
    )

    assert task_run.updates == []
