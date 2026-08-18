"""P1-17 / P1-FN-06: "已分发但未上报 RUNNING" 的僵尸分发判定（带活性证据）。

触发场景：``scheduler_loop._record_dispatch_result`` 把 dispatch_status 置为
DISPATCHED 后，Worker 应主动上报一次 running 把 runtime_status 推到 RUNNING。
Worker 在"收到任务 → 上报 RUNNING"窗口崩溃时，master 侧表现为：

- dispatch_status = DISPATCHING / DISPATCHED
- runtime_status  = NULL / QUEUED
- dispatch_updated_at 老于超时阈值

通过 ``update_dispatch_status(FAILED)`` 统一收敛终态，由 status_service 的
CAS 保证：迟到 RUNNING 命中 dispatch 终态吸收不会翻转；迟到 SUCCESS 也
不会复活（runtime 终态保护）。

P1-FN-06：transport 层 ACK 不代表 PG 已写入 RUNNING —— result loop 积压时
真实 Worker 可能还在跑。因此判死前先取活性证据：该 run 绑定 Worker 的
Lease 仍有效（未过期）则跳过并延长观察（Worker 存活时其终态上报 / ready
stream PEL 重投最终会收敛该 run）；只有 Lease 已死或 run 从未绑定 Worker
才允许按超时判失败。活性证据不可得（Redis/Lease 存储故障）时本轮显式
跳过判死 —— 缺证据即判失败会误杀存活 Worker 上的长任务；下一轮 reconcile
会重试，不构成静默丢失。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from antcode_core.domain.models import TaskRun
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus
from loguru import logger
from tortoise.expressions import Q

from antcode_master.control.reconcile_failure_settlement import settle_dispatch_failure_snapshot
from antcode_master.control.retry_intent_delivery import RetryIntentDeliveryError
from antcode_master.control.scheduler_authority import SchedulerAuthorityLost

# 单轮扫描的候选 run 上限（与原 reconcile 行为一致）。
NO_ACK_SCAN_LIMIT = 200


async def check_dispatched_no_ack(timeout_seconds: int, authority_token: int) -> None:
    """按 ``timeout_seconds`` 判定僵尸分发；Worker Lease 存活的 run 延长观察。

    P1-FN-11: 活性证据从"Worker 级"升级到"(worker_id, lease_id) 代际级"。
    Worker 换代后旧代际的 run 已在其他 Worker 上被 XAUTOCLAIM 接管,不应
    因当前 Worker 有活代际 Lease 就跳过判死。
    """
    try:
        now = datetime.now(UTC)
        marked, scanned, skipped_alive = await _scan_no_ack_candidates(
            cutoff=now - timedelta(seconds=int(timeout_seconds)),
            authority_token=authority_token,
            now=now,
            timeout_seconds=timeout_seconds,
        )
        logger.info(
            f"P1-17: 已标记 {marked}/{scanned} 条僵尸分发为 FAILED"
            f" (P1-FN-11: {skipped_alive} 条因 Worker 代际匹配的 Lease 存活延长观察)"
        )
    except (SchedulerAuthorityLost, RetryIntentDeliveryError):
        raise
    except Exception:
        logger.exception("P1-17: 检测僵尸分发失败")


async def _scan_no_ack_candidates(*, cutoff, authority_token, now, timeout_seconds):
    cursor = 0
    totals = [0, 0, 0]
    while True:
        candidates = await _load_no_ack_candidates(cutoff, after_id=cursor)
        if not candidates:
            return tuple(totals)
        alive = await load_alive_lease_by_worker({run.worker_id for run in candidates if run.worker_id})
        marked, skipped = await _converge_dead_candidates(
            candidates,
            authority_token=authority_token,
            alive_lease_by_worker=alive,
            now=now,
            timeout_seconds=timeout_seconds,
        )
        totals[0] += marked
        totals[1] += len(candidates)
        totals[2] += skipped
        cursor = candidates[-1].id
        if len(candidates) < NO_ACK_SCAN_LIMIT:
            return tuple(totals)


async def _load_no_ack_candidates(cutoff: datetime, *, after_id: int) -> list[TaskRun]:
    """dispatch 已发出、runtime 未进入 RUNNING、超阈值的候选 run。"""
    return (
        await TaskRun.filter(
            Q(
                dispatch_status__in=[
                    DispatchStatus.DISPATCHING,
                    DispatchStatus.DISPATCHED,
                ]
            ),
            Q(runtime_status__isnull=True) | Q(runtime_status=RuntimeStatus.QUEUED),
            Q(dispatch_updated_at__lt=cutoff) | Q(dispatch_updated_at__isnull=True, created_at__lt=cutoff),
        )
        .filter(id__gt=after_id)
        .order_by("id")
        .only(
            "id",
            "run_id",
            "worker_id",
            "lease_id",
            "dispatch_status",
            "runtime_status",
            "dispatch_updated_at",
            "scheduler_fencing_token",
        )
        .limit(NO_ACK_SCAN_LIMIT)
        .all()
    )


async def _converge_dead_candidates(
    candidates: list[TaskRun],
    *,
    authority_token: int,
    alive_lease_by_worker: dict[int, str],
    now: datetime,
    timeout_seconds: int,
) -> tuple[int, int]:
    marked = 0
    skipped_alive = 0
    for run in candidates:
        if owning_generation_is_alive(run, alive_lease_by_worker):
            skipped_alive += 1
            continue
        ok = await settle_dispatch_failure_snapshot(
            run,
            authority_token,
            error_message=(f"节点未在 {timeout_seconds}s 内上报 RUNNING(worker 可能在收到任务后崩溃)"),
            status_at=now,
        )
        if ok:
            marked += 1
    return marked, skipped_alive


def owning_generation_is_alive(run: TaskRun, alive_lease_by_worker: dict[int, str]) -> bool:
    """P1-FN-11: 持有该 run 的 (worker_id, lease_id) 代际是否仍可能推进它。

    - 未绑定 Worker：没有代际可言，不构成存活证据。
    - 已绑定 lease_id：只有当前活代际与之精确一致才算存活；换代后旧 run 已
      被剥夺归属（其结果由新代际的 XAUTOCLAIM 接管或彻底消失）。
    - 未绑定 lease_id（dispatch 后 Worker 尚未 claim）：回退到 Worker 级活性，
      有任何活代际就算存活。

    这是全仓"该 run 是否还可能推进"的唯一判据，僵尸分发与取消悬挂共用，
    避免两处各写一份互相漂移的活性定义。
    """
    if not run.worker_id:
        return False
    alive_lease = alive_lease_by_worker.get(run.worker_id)
    if run.lease_id:
        return alive_lease == run.lease_id
    return alive_lease is not None


async def load_alive_lease_by_worker(worker_internal_ids: set[int]) -> dict[int, str]:
    """P1-FN-11: 返回 {worker_internal_id: current_lease_id} 的映射。

    从 Worker 级活性(bool 集合)升级到代际级(具体 lease_id),让 caller
    能区分"Worker 换代后旧 run 已孤儿"vs"Worker 当前代际仍持有该 run"。
    include_expired=False 保证过期残留记录不会被误判为存活。任意存储故障
    向上抛,由调用方决定本轮是否跳过判死。
    """
    if not worker_internal_ids:
        return {}

    from antcode_core.application.services.lease_service import LeaseStore
    from antcode_core.domain.models import Worker
    from antcode_core.infrastructure.redis.client import get_redis_client
    from antcode_core.infrastructure.redis.control_plane import redis_namespace

    rows = await Worker.filter(id__in=list(worker_internal_ids)).only("id", "public_id").all()
    redis = await get_redis_client()
    store = LeaseStore(redis, namespace=redis_namespace())
    alive: dict[int, str] = {}
    for row in rows:
        lease = await store.get(row.public_id, include_expired=False)
        if lease is not None:
            alive[row.id] = lease.lease_id
    return alive


__all__ = [
    "NO_ACK_SCAN_LIMIT",
    "check_dispatched_no_ack",
    "load_alive_lease_by_worker",
    "owning_generation_is_alive",
]
