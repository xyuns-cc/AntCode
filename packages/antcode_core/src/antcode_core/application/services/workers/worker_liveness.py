"""Worker 存活判定的时间语义：时区归一、心跳新鲜度、首租约引导窗口。

单独成文而不是塞进 ``worker_heartbeat_service``：这三件事共同定义"什么算活着"，
且引导窗口的正确性依赖 ``worker_lease_authority.LEASE_ELIGIBLE_STATUSES`` 与心跳
判活之间的耦合，是一条需要被单独读懂、单独测试的分布式启动边界。

死锁背景：Lease 资格白名单是 ``{connecting, online}``，而 Worker 必须先拿到
Lease 才会开始上报心跳。心跳监控若把"从未上报过心跳"一律判为离线，新注册的
Worker 就会在注册后一两秒内从引导态 ``connecting`` 被打成 ``offline``，此后
Lease 签发恒 409——拿不到 Lease → 不会有心跳 → 永远不合格，闭环。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from antcode_core.domain.models import WorkerStatus


def naive_datetime(value: datetime | None) -> datetime | None:
    """统一去掉时区信息，让 Redis / PostgreSQL / 本地时钟三方可比。"""
    if value is not None and value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def is_within_bootstrap_window(
    worker: Any,
    old_status: WorkerStatus | str,
    window_seconds: float,
) -> bool:
    """判断 Worker 是否仍处于"等待首个 Lease"的引导窗口内。

    仅当三个条件同时成立才返回 True：仍是 ``connecting`` 引导态、从未上报过
    心跳、且距离进入该状态未超过 ``window_seconds``。窗口有界是关键——注册后
    立刻挂掉的 Worker 最终仍会被标记离线，不会永久停在 ``connecting``。
    """
    status_value = old_status.value if isinstance(old_status, WorkerStatus) else str(old_status).strip().lower()
    if status_value != WorkerStatus.CONNECTING.value:
        return False
    if worker.last_heartbeat is not None:
        return False
    anchor = naive_datetime(worker.updated_at or worker.created_at)
    if anchor is None:
        return False
    return (datetime.now() - anchor).total_seconds() <= window_seconds


def heartbeat_is_fresh(heartbeat: datetime | None, now: datetime, timeout_seconds: float) -> bool:
    """心跳是否仍在超时窗口内。``None`` 一律视为不新鲜。"""
    return bool(heartbeat and (now - heartbeat).total_seconds() <= timeout_seconds)


# 从未上报过心跳时用负数，而不是 0 或某个大数：0 会读成"刚刚才心跳过"，
# 大数又会读成一个具体的年龄。负数在任何单位下都不是合法年龄，只能读成"没有"。
HEARTBEAT_AGE_UNKNOWN_MS = -1
_MS_PER_SECOND = 1000


def heartbeat_age_ms(heartbeat: datetime | None) -> int:
    """距上次心跳过去了多少毫秒。这是**新鲜度**，不是网络往返时延。"""
    normalized = naive_datetime(heartbeat)
    if normalized is None:
        return HEARTBEAT_AGE_UNKNOWN_MS
    return int((datetime.now() - normalized).total_seconds() * _MS_PER_SECOND)


__all__ = [
    "HEARTBEAT_AGE_UNKNOWN_MS",
    "heartbeat_age_ms",
    "heartbeat_is_fresh",
    "is_within_bootstrap_window",
    "naive_datetime",
]
