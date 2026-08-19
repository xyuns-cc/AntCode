"""收敛"已请求取消、但持有它的 Worker 代际已消失"的执行。

取消链路对已分配 run 的契约是三步（见 ``run_cancel_support`` 模块注释）：
``record_cancel_request`` 落库 → 向 Worker 投递 control → **由 Worker 的真实
结算结果写入 CANCELLED**。API 刻意不提前制造终态，这是对的。

但第三步没有兜底所有者：Worker 在处理 control 之前换代或消失时，那条 control
再也不会被消费（新代际的本地队列里没有这个 run），run 永久停在非终态。所有失败
reaper 又都以 ``cancel_requested_at is None`` 为前置条件
（``failure_settlement._matches_request``）——它们不能把取消中的 run 判成 FAILED，
否则终态归属就从取消平面被偷走。于是没有任何一方负责收敛，run 永久悬挂，
``quiesce_worker_for_delete`` 因此永远 409，节点删不掉。

本模块补上这个所有者，且只在**证据充分**时动手：判据与僵尸分发共用同一个
``owning_generation_is_alive`` —— 代际仍活 = Worker 还可能处理这条 control，
本轮跳过；代际已死 = 不可能再推进，按取消方承诺的终态写 CANCELLED。
真正还在执行的 run 代际必然匹配，因此删除守卫的 fail-closed 语义不受影响。

未分配 run（``worker_id`` 为空）由 ``execution_status_service.cancel_unassigned_run``
在取消请求当场同步收敛，不属于本模块。
"""

from __future__ import annotations

from datetime import UTC, datetime

from antcode_core.application.services.scheduler.execution_status_service import (
    execution_status_service,
)
from antcode_core.domain.models import TaskRun
from antcode_core.domain.models.enums import RuntimeStatus
from antcode_core.domain.models.task_status_sets import TASK_RUN_ACTIVE_STATUSES
from loguru import logger

from antcode_master.control.dispatch_ack_liveness import (
    load_alive_lease_by_worker,
    owning_generation_is_alive,
)
from antcode_master.control.scheduler_authority import execute_with_scheduler_authority

# 单轮扫描的候选 run 上限，与 ``NO_ACK_SCAN_LIMIT`` 同量级。
CANCEL_SCAN_LIMIT = 200
ABANDONED_CANCEL_ERROR = "取消请求已下发，但持有该执行的 Worker 代际已消失"


async def settle_abandoned_cancellations(authority_token: int) -> None:
    """把"取消已请求、归属代际已死"的 run 收敛为 CANCELLED。

    活性证据不可得（Redis / Lease 存储故障）时 ``load_alive_lease_by_worker``
    直接抛出：缺证据即判死会误杀存活 Worker 上真正在跑的 run，本轮什么都不做、
    下一轮 reconcile 重试才是 fail-closed 的正确行为。
    """
    cursor = 0
    settled = 0
    scanned = 0
    while True:
        candidates = await _load_cancelled_candidates(after_id=cursor)
        if not candidates:
            break
        alive = await load_alive_lease_by_worker({run.worker_id for run in candidates if run.worker_id})
        settled += await _settle_dead_generation_runs(candidates, alive, authority_token)
        scanned += len(candidates)
        cursor = candidates[-1].id
        if len(candidates) < CANCEL_SCAN_LIMIT:
            break
    if scanned:
        logger.info(f"取消悬挂收敛: 已结算 {settled}/{scanned} 条（其余归属代际仍存活，延长观察）")


async def _load_cancelled_candidates(*, after_id: int) -> list[TaskRun]:
    """已记录取消请求、已绑定 Worker、仍处于非终态的 run。

    状态集合复用 ``TASK_RUN_ACTIVE_STATUSES`` —— 删除守卫拦的就是这一组，
    收敛范围与阻塞范围必须是同一个定义。
    """
    return (
        await TaskRun.filter(
            cancel_requested_at__isnull=False,
            worker_id__isnull=False,
            status__in=list(TASK_RUN_ACTIVE_STATUSES),
            id__gt=after_id,
        )
        .order_by("id")
        .only("id", "run_id", "worker_id", "lease_id")
        .limit(CANCEL_SCAN_LIMIT)
        .all()
    )


async def _settle_dead_generation_runs(
    candidates: list[TaskRun],
    alive_lease_by_worker: dict[int, str],
    authority_token: int,
) -> int:
    now = datetime.now(UTC)
    settled = 0
    for run in candidates:
        if owning_generation_is_alive(run, alive_lease_by_worker):
            continue
        updated = await execute_with_scheduler_authority(
            authority_token,
            execution_status_service.update_runtime_status,
            run_id=run.run_id,
            status=RuntimeStatus.CANCELLED,
            status_at=now,
            error_message=ABANDONED_CANCEL_ERROR,
        )
        settled += int(bool(updated))
    return settled


__all__ = ["ABANDONED_CANCEL_ERROR", "CANCEL_SCAN_LIMIT", "settle_abandoned_cancellations"]
