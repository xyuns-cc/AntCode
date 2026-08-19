"""Prevent run deletion while the executing Worker generation can still act.

ownership key 的阻塞价值完全取决于**持有它的代际是否还活着**：

- 代际仍活 —— Worker 可能正在跑这条 run（PG 已被 eviction / no-ACK / 取消
  平面写成终态并不代表进程停了），删掉行会丢失与在途执行的关联。必须拦。
- 代际已死 —— 它无法续期 ownership（``_RENEW_SCRIPT`` 返回 -1）、无法提交
  非终态结果（``TaskResultCommitter._commit_locked``）、Worker 自身也会
  ``_is_current_generation`` 自锁停机。这把键只是"没人来跑 release"的残留，
  与 Worker 正常收尾后立即释放的情形在风险面上完全等价，不该再阻塞删除。

因此本模块**不释放**任何 ownership key：围栏一个字节都不动，只是不再把死代际
的残留读成"仍在结算"。真正的重复执行互斥仍由 ``claim_run_ownership``
（含 ``_TAKEOVER_SCRIPT`` 死主接管）与 TTL 负责。

活性判据直接用 ``LeaseStore.is_current`` —— ownership Lua 自己声明"与
``LeaseStore.is_current`` 完全一致"（见 ``run_ownership_fence_lua``），
``owning_generation_is_alive`` / result loop / Worker 自锁也都归约到它。
它比 ``owning_generation_is_alive`` 更严（额外查 revoked 集与 retention
余量），且直接判 owner key 里记录的持有者，不依赖 PG 对持有者的转述。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from antcode_core.application.services.lease_service import LeaseStore
from antcode_core.application.services.workers.run_ownership_fence import (
    parse_ownership_token,
    run_owner_key,
)
from antcode_core.domain.models.task_status_sets import TASK_RUN_TERMINAL_STATUSES
from antcode_core.infrastructure.redis import get_redis_client, redis_namespace

GenerationProbe = Callable[[str, str], Awaitable[bool]]

OWNERSHIP_LOOKUP_BATCH_SIZE = 200
SETTLEMENT_STORE_UNAVAILABLE = "执行结算状态服务不可用"
OWNERSHIP_TOKEN_UNREADABLE = "执行归属记录无法解析，状态服务不可用，无法确认持有代际"


class RunSettlementPendingError(ValueError):
    """At least one terminal run is still settling on a Worker."""


class RunSettlementGuardUnavailable(RuntimeError):
    """The ownership store could not provide an authoritative answer."""


def _normalize_run_ids(run_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(run_id) for run_id in run_ids if run_id))


def _live_holder_message(run_id: str, worker_id: str) -> str:
    return f"执行 {run_id} 仍由在线 Worker {worker_id} 持有，请等待其上报结算结果后再删除"


class _GenerationLiveness:
    """一次 guard 调用内的代际活性判定；同一代际只探测一次。

    一个崩溃 Worker 的全部残留 ownership 共享同一个 ``worker:lease`` token，
    缓存把"每条 run 一次 Redis 往返"压成"每个代际一次"，避免删除大项目时在
    事务里打出上万次探测。
    """

    def __init__(self, probe: GenerationProbe) -> None:
        self._probe = probe
        self._verdicts: dict[tuple[str, str], bool] = {}

    async def is_alive(self, worker_id: str, lease_id: str) -> bool:
        generation = (worker_id, lease_id)
        if generation not in self._verdicts:
            self._verdicts[generation] = await self._probe_once(worker_id, lease_id)
        return self._verdicts[generation]

    async def _probe_once(self, worker_id: str, lease_id: str) -> bool:
        try:
            return bool(await self._probe(worker_id, lease_id))
        except Exception as exc:
            raise RunSettlementGuardUnavailable(SETTLEMENT_STORE_UNAVAILABLE) from exc


async def _resolve_redis(redis_client: Any | None) -> Any:
    if redis_client is not None:
        return redis_client
    try:
        return await get_redis_client()
    except Exception as exc:
        raise RunSettlementGuardUnavailable(SETTLEMENT_STORE_UNAVAILABLE) from exc


async def _fetch_owners(redis: Any, batch: tuple[str, ...]) -> list[Any]:
    try:
        owners = await redis.mget([run_owner_key(run_id) for run_id in batch])
        if len(owners) != len(batch):
            raise RuntimeError("Redis MGET 返回数量不匹配")
    except Exception as exc:
        raise RunSettlementGuardUnavailable(SETTLEMENT_STORE_UNAVAILABLE) from exc
    return list(owners)


async def _reject_live_holders(
    batch: tuple[str, ...],
    owners: list[Any],
    liveness: _GenerationLiveness,
) -> None:
    """只有"持有者代际仍活"才算未结算；死代际残留放行且不被清除。"""
    for run_id, raw_owner in zip(batch, owners, strict=True):
        if raw_owner is None:
            continue
        holder = parse_ownership_token(raw_owner)
        if holder is None:
            raise RunSettlementGuardUnavailable(OWNERSHIP_TOKEN_UNREADABLE)
        if await liveness.is_alive(*holder):
            raise RunSettlementPendingError(_live_holder_message(run_id, holder[0]))


async def ensure_runs_settled(
    run_ids: Iterable[str],
    *,
    redis_client: Any | None = None,
    generation_probe: GenerationProbe | None = None,
) -> None:
    """Fail closed while any ownership key is held by a still-live generation."""
    normalized = _normalize_run_ids(run_ids)
    if not normalized:
        return
    redis = await _resolve_redis(redis_client)
    probe = generation_probe if generation_probe is not None else _lease_generation_probe(redis)
    liveness = _GenerationLiveness(probe)
    for offset in range(0, len(normalized), OWNERSHIP_LOOKUP_BATCH_SIZE):
        batch = normalized[offset : offset + OWNERSHIP_LOOKUP_BATCH_SIZE]
        owners = await _fetch_owners(redis, batch)
        await _reject_live_holders(batch, owners, liveness)


def _lease_generation_probe(redis: Any) -> GenerationProbe:
    """权威代际活性判据；与 ownership Lua 的 lease 校验同一个实现。"""
    return LeaseStore(redis, namespace=redis_namespace()).is_current


async def load_deletable_run_ids(connection: Any, task_ids: Iterable[int]) -> list[str]:
    """Load terminal runs and verify ownership while caller holds Task locks."""
    from antcode_core.domain.models.task_run import TaskRun

    normalized_tasks = tuple(dict.fromkeys(task_ids))
    if not normalized_tasks:
        return []
    runs = TaskRun.filter(task_id__in=list(normalized_tasks)).using_db(connection)
    if await runs.exclude(status__in=list(TASK_RUN_TERMINAL_STATUSES)).exists():
        raise ValueError("任务存在未终态执行，请先取消并等待执行结束")
    rows = await runs.only("run_id").all()
    run_ids = [row.run_id for row in rows if row.run_id]
    await ensure_runs_settled(run_ids)
    return run_ids


__all__ = [
    "OWNERSHIP_TOKEN_UNREADABLE",
    "SETTLEMENT_STORE_UNAVAILABLE",
    "GenerationProbe",
    "RunSettlementGuardUnavailable",
    "RunSettlementPendingError",
    "ensure_runs_settled",
    "load_deletable_run_ids",
]
