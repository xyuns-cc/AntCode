"""回答"这批任务现在有没有一台能立刻接手的 Worker"。

从 ``worker_dispatcher`` 拆出来的理由是它只有一种失效形状：**容量不足**——要么筛不出
候选（离线/过载/能力不匹配/区域标签不符），要么选中的那台心跳已经过期。这类失败是"稍后
再试"，与队列写不进去、栅栏不成立那种"我们坏了"完全不同。两者混在一处，调用方就只能靠
错误文案去猜自己该重试还是该报警。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from antcode_core.application.services.workers.dispatch_error_codes import (
    DISPATCH_NO_CAPACITY,
    DISPATCH_WORKER_OFFLINE,
)
from antcode_core.application.services.workers.worker_capability_routing import capability_requirement_label
from antcode_core.application.services.workers.worker_selection import select_dispatch_worker
from antcode_core.domain.models import WorkerStatus


@dataclass(frozen=True)
class DispatchAdmission:
    """准入结论。``worker`` 为空即被拒，此时必须同时给出结构化码与人读原因。"""

    worker: Any | None
    error: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.worker is None and not (self.error and self.error_code):
            raise ValueError("准入被拒时必须给出 error_code 与原因")


async def worker_heartbeat_is_fresh(worker: Any) -> bool:
    """确保节点在线（依赖心跳状态）"""
    from antcode_core.application.services.workers.worker_heartbeat_service import WorkerHeartbeatService

    if worker.status != WorkerStatus.ONLINE:
        return False
    if worker.last_heartbeat is None:
        return False

    last_hb = worker.last_heartbeat
    if last_hb.tzinfo is not None:
        last_hb = last_hb.astimezone().replace(tzinfo=None)
    return (datetime.now() - last_hb).total_seconds() <= WorkerHeartbeatService.HEARTBEAT_TIMEOUT


def _no_worker_reason(require_task_type: str | frozenset[str] | None) -> str:
    if not require_task_type:
        return "无可用 Worker"
    label = capability_requirement_label(require_task_type)
    return f"无支持 task_type={label} 的 Worker (检查 worker 侧插件是否装载)"


async def admit_dispatch_worker(
    load_balancer: Any,
    *,
    worker_id: str | int | None = None,
    region: str | None = None,
    tags: list[str] | None = None,
    require_render: bool = False,
    require_task_type: str | frozenset[str] | None = None,
) -> DispatchAdmission:
    """挑一台并确认它现在真的还在线；两步都过了才算准入。"""
    worker = await select_dispatch_worker(
        load_balancer,
        worker_id=worker_id,
        region=region,
        tags=tags,
        require_render=require_render,
        require_task_type=require_task_type,
    )
    if not worker:
        return DispatchAdmission(
            worker=None,
            error=_no_worker_reason(require_task_type),
            error_code=DISPATCH_NO_CAPACITY,
        )
    if not await worker_heartbeat_is_fresh(worker):
        return DispatchAdmission(
            worker=None,
            error=f"Worker 未在线: {worker.name}",
            error_code=DISPATCH_WORKER_OFFLINE,
        )
    return DispatchAdmission(worker=worker)


__all__ = ["DispatchAdmission", "admit_dispatch_worker", "worker_heartbeat_is_fresh"]
