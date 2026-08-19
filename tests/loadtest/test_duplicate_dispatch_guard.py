"""Local guards for the duplicate-dispatch oracle.

调度平台的核心正确性是"同一个 task 绝不被派发成两个 run 执行两次"。
外部可观测形态只有一个：该 task 的 run 历史里多出一条。而最新那条照样
success，所以只读 ``size=1`` 的断言对它完全免疫——这里把这条判据本身钉住。
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.loadtest.tool.scenarios import reject_duplicate_runs, wait_for_successful_runs


class _DuplicateRunApi:
    """同一个 task 返回两条 run —— 重复派发的外部可观测形态。"""

    async def task_runs(self, task_id: str, _index: int = 0, *, size: int = 1) -> list[dict[str, Any]]:
        return [
            {"status": "success", "worker_id": "worker-1", "run_id": f"{task_id}-b"},
            {"status": "success", "worker_id": "worker-1", "run_id": f"{task_id}-a"},
        ]


@pytest.mark.asyncio
async def test_duplicate_dispatch_is_rejected_even_when_latest_run_succeeded() -> None:
    with pytest.raises(AssertionError, match="dispatched more than once"):
        await wait_for_successful_runs(
            _DuplicateRunApi(),  # type: ignore[arg-type]
            ("task-1",),
            1,
            expected_worker_id="worker-1",
        )


def test_reject_duplicate_runs_accepts_exactly_one_run_per_task() -> None:
    reject_duplicate_runs({"task-1": [{"status": "success"}], "task-2": []})
    with pytest.raises(AssertionError, match="task-2"):
        reject_duplicate_runs({"task-1": [{"status": "success"}], "task-2": [{}, {}]})
