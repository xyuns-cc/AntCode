"""调度面不发布实时 ``run_status`` 帧的边界回归。

``SchedulerService._push_execution_status`` 曾是 ``pass`` 空桩，却被三处生产
代码 await（派发开始 / 派发成功 / 派发失败），调用方以为自己推送了状态。
空桩与三处调用点已删除：实时 ``run_status`` 帧的唯一发布者是
``ingester/run_status_publisher``，且只由 Worker 结果回传触发。

本文件同时证明"删干净了"与"没删错东西"：
- 三个控制面模块的源码里不再有空桩/发布调用；
- 真实的派发失败结算路径跑完，SSE 传输层一帧都没收到；
- 同一个传输层录制器在 Worker 回传路径上确实能收到帧（非空断言，避免
  上面的"零帧"结论因为录制器失效而变成假阳性）。
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from antcode_core.domain.models.enums import DispatchStatus, ScheduleType, TaskStatus, TaskType
from antcode_core.domain.models.scheduler_authority import SchedulerAuthority
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.infrastructure.redis import sse_event_stream
from antcode_master.control import retry_dispatch_recovery, scheduler_failure_wiring, scheduler_loop
from antcode_master.ingester.run_status_publisher import publish_persisted_run_status
from tortoise import Tortoise

TOKEN = 31
CONTROL_PLANE_MODULES = (scheduler_loop, scheduler_failure_wiring, retry_dispatch_recovery)


def test_status_push_stub_and_call_sites_are_gone() -> None:
    assert not hasattr(scheduler_loop.SchedulerService, "_push_execution_status")
    for module in CONTROL_PLANE_MODULES:
        source = inspect.getsource(module)
        assert "await self._push_execution_status" not in source
        assert "await service._push_execution_status" not in source
        assert "def _push_execution_status" not in source
        # 控制面也不得绕过已删除的空桩自己发帧
        assert "publish_sse_event" not in source
        assert "publish_persisted_run_status" not in source


@pytest_asyncio.fixture
async def scheduler_database():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={
            "models": [
                "antcode_core.domain.models.scheduler_authority",
                "antcode_core.domain.models.task",
                "antcode_core.domain.models.task_run",
            ]
        },
    )
    await Tortoise.generate_schemas()
    await SchedulerAuthority.create(name="master", fencing_token=TOKEN, activated_at=datetime.now(UTC))
    try:
        yield
    finally:
        await Tortoise.close_connections()


@pytest.fixture
def recorded_sse_frames(monkeypatch) -> list[dict[str, Any]]:
    """录制真实 ``publish_sse_event`` 写入 Redis 的帧（只替换传输层）。"""
    frames: list[dict[str, Any]] = []

    async def _eval(_script: str, _key_count: int, *args: Any) -> list[Any]:
        payload = args[4]
        frames.append(sse_event_stream.decode_sse_event({"payload": payload}))
        return ["1-0", len(payload), 0, 1]

    async def _client() -> Any:
        return type("_Redis", (), {"eval": staticmethod(_eval)})()

    monkeypatch.setattr(sse_event_stream, "get_redis_client", _client)
    return frames


@pytest.mark.asyncio
async def test_dispatch_failure_settles_without_realtime_frame(
    scheduler_database,
    recorded_sse_frames,
    monkeypatch,
) -> None:
    """派发失败落到 FAILED 终态，但调度面一帧实时状态都不发。"""
    task = await Task.create(
        name="no-frame-task",
        project_id=1,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=1,
    )
    execution = await TaskRun.create(
        task_id=task.id,
        run_id="run-no-frame",
        status=TaskStatus.DISPATCHING,
        dispatch_status=DispatchStatus.DISPATCHING,
        scheduler_fencing_token=TOKEN,
    )
    service = scheduler_loop.SchedulerService()
    service._log_execution = AsyncMock()
    service._finalize_stats = AsyncMock()
    service._dispatch_and_run = AsyncMock(return_value={"success": False, "error": "queue unavailable"})
    monkeypatch.setattr(scheduler_failure_wiring, "deliver_retry_intent", AsyncMock())

    await retry_dispatch_recovery.run_prepared_execution(
        service,
        task.id,
        (task, object(), object(), execution, datetime.now(UTC)),
    )

    persisted = await TaskRun.get(id=execution.id)
    assert persisted.status == TaskStatus.FAILED
    assert persisted.dispatch_status == DispatchStatus.FAILED
    assert recorded_sse_frames == []

    # 非空对照：同一个录制器在 Worker 回传路径（result_loop 的唯一发布点）上
    # 确实会收到一帧真实的 run_status，说明上面的"零帧"不是录制器坏了。
    await publish_persisted_run_status(execution.run_id)
    assert [frame["type"] for frame in recorded_sse_frames] == ["run_status"]
    assert recorded_sse_frames[0]["run_id"] == execution.run_id
    assert recorded_sse_frames[0]["data"]["status"] == TaskStatus.FAILED.value
