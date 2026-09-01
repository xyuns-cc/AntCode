"""读回一台 Worker 的实时负载指标，供派发排序与硬门禁使用。

**"读不到"与"没上报过"不是一件事，两者也都不是"这台机器很忙"。** 从 worker_dispatcher
拆出来就是为了把这三件事分开：

- **Redis 读失败**（连不上 / 超时 / 认证被拒 / NOSCRIPT ……）：我们对这台机器一无所知。
  从前这里是一个全 ``except`` 兜成 ``metrics = {}``，而空 dict 交给
  ``normalize_worker_metrics`` 会被补成 cpu=100 —— 等价于替这台机器宣布"它最忙"。
  Redis 抖一下就是**所有** Worker 同时被这样宣布，于是 ``is_worker_available`` 把它们
  全部踢出候选，派发停摆；而那个 Redis 异常连一行日志都没有，运维在日志里只看得到一句
  "无符合条件节点"，指向的方向完全是错的。所以这里抛 ``WorkerMetricsUnavailableError``，
  由调用方原样暴露："我读不到"必须长得和"它很忙"不一样。
- **Redis 连上了，心跳 hash 是空的**：这是关于这台 Worker 的真实结论——它没上报过，或者
  心跳已经过期。据此 fail-closed 地判它不可用是对的（``normalize_worker_metrics`` 缺项
  补 100 就是这条策略），但必须说得出是这个原因，所以单独打一条 WARNING。
- **指标本身读得到但是坏的**（``cpu="abc"`` 之类）：一台机器的数据问题，不牵连别人。

三者在派发日志里必须能分辨，否则"任务不动了"这件事永远查不出根因。
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from loguru import logger

from antcode_core.application.services.workers.worker_metrics import normalize_worker_metrics
from antcode_core.infrastructure.redis import worker_heartbeat_key


class WorkerMetricsUnavailableError(RuntimeError):
    """负载指标读不回来。

    这**不是**"这台机器忙"，也**不是**"这台机器空闲"——是我们对它一无所知。调用方
    可以据此拒绝派发，但不许把它折算成任何一个具体的负载读数。
    """

    def __init__(self, *, worker_name: str, heartbeat_key: str, cause: BaseException) -> None:
        super().__init__(
            f"Worker [{worker_name}] 的负载指标读不回来: key={heartbeat_key} cause={type(cause).__name__}: {cause}"
        )
        self.worker_name = worker_name
        self.heartbeat_key = heartbeat_key
        self.cause = cause


def _decode(value: Any) -> Any:
    return value.decode() if isinstance(value, bytes) else value


async def _read_heartbeat_hash(worker: Any) -> dict[Any, Any]:
    """读 Redis 心跳 hash；读失败抛 ``WorkerMetricsUnavailableError``，绝不兜成空。"""
    from antcode_core.infrastructure.redis import get_redis_client

    heartbeat_key = worker_heartbeat_key(worker.public_id)
    try:
        redis = await get_redis_client()
        raw = await cast(Awaitable[dict[Any, Any]], redis.hgetall(heartbeat_key))
    except Exception as exc:
        error = WorkerMetricsUnavailableError(
            worker_name=worker.name,
            heartbeat_key=heartbeat_key,
            cause=exc,
        )
        # ERROR 而不是 WARNING：这是基础设施故障，不是某台机器的状态。
        logger.error("负载指标读取失败，该 Worker 本轮不参与派发: {}", error)
        raise error from exc

    if not raw:
        # 连得上、hash 是空的 —— 关于这台 Worker 的真实结论，与"读不到"必须分开说。
        logger.warning(
            "Worker [{}] 没有心跳指标可读（未上报过或心跳已过期），按不可用处理: key={}",
            worker.name,
            heartbeat_key,
        )
    return raw


async def probe_worker_resources(worker: Any) -> dict[str, float | int]:
    """返回归一化后的负载指标；Redis 读不到时抛 ``WorkerMetricsUnavailableError``。"""
    persisted = worker.metrics if isinstance(worker.metrics, Mapping) else {}
    if persisted:
        return normalize_worker_metrics(persisted)

    raw = await _read_heartbeat_hash(worker)
    return normalize_worker_metrics({_decode(key): _decode(value) for key, value in raw.items()})


def merge_worker_metrics(persisted: Any, probed: Any) -> dict[str, Any]:
    """落库指标打底、实时探测覆盖。"""
    merged: dict[str, Any] = {}
    if persisted:
        merged.update(persisted)
    if probed:
        merged.update(probed)
    return merged


@dataclass(frozen=True)
class DispatchCandidates:
    """一轮候选筛选的结果，以及"为什么没进候选"的分类。

    ``unreadable`` 单独留一栏，是因为它与其余落选理由性质完全不同：那几台不是"忙"，
    是"我们读不到"。两者混在一起，Redis 抖动就会以"无符合条件节点"的面目出现。
    """

    scored: tuple[tuple[Any, dict[str, Any]], ...]
    unreadable: tuple[str, ...]


def collect_dispatch_candidates(
    balancer: Any,
    workers: Sequence[Any],
    resource_results: Sequence[Any],
) -> DispatchCandidates:
    """把探测结果分成候选、指标读不回来的、以及其余落选的。"""
    scored: list[tuple[Any, dict[str, Any]]] = []
    unreadable: list[str] = []
    for worker, probed in zip(workers, resource_results, strict=False):
        if isinstance(probed, WorkerMetricsUnavailableError):
            unreadable.append(worker.name)
            continue
        if isinstance(probed, BaseException) or not probed:
            logger.warning("Worker [{}] 的负载指标不可用，本轮不参与派发: {}", worker.name, probed)
            continue
        metrics = merge_worker_metrics(worker.metrics, probed)
        if not balancer.is_worker_available(worker, metrics):
            logger.debug(f"节点不可用 [{worker.name}]")
            continue
        scored.append((worker, metrics))
    return DispatchCandidates(scored=tuple(scored), unreadable=tuple(unreadable))


def probe_failure_suffix(candidates: DispatchCandidates) -> str:
    """给"无符合条件节点"补上真正的原因；没有指标故障时返回空串，措辞不变。"""
    if not candidates.unreadable:
        return ""
    return f"；其中 {len(candidates.unreadable)} 台是负载指标读不回来（{', '.join(candidates.unreadable)}），不是它们忙"


def warn_unreadable_workers(candidates: DispatchCandidates) -> None:
    """哪怕还剩得下候选，指标读不回来也必须出声——它是基础设施故障。"""
    if not candidates.unreadable:
        return
    logger.error(
        "{} 台 Worker 的负载指标读不回来，本轮派发已把它们排除（这不是它们忙）: {}",
        len(candidates.unreadable),
        ", ".join(candidates.unreadable),
    )


__all__ = [
    "DispatchCandidates",
    "WorkerMetricsUnavailableError",
    "collect_dispatch_candidates",
    "merge_worker_metrics",
    "probe_failure_suffix",
    "probe_worker_resources",
    "warn_unreadable_workers",
]
