"""Run ownership fence keyed to the authoritative Worker Lease generation.

Gateway 与 Direct Worker 共用的 run 执行互斥原语。三个 Lua 脚本把
"lease 仍是当前代际" 的校验放进与写入同一个原子步骤，堵死此前
check-then-act 的 TOCTOU：旧代际进程通过外部校验后再切代，仍会在
脚本内被权威 Lease Hash 拒绝，无法创建/续期 ownership 阻塞新代际。

Key 布局（与 ``LeaseStore`` 同一个 ``{<ns>}`` hash tag，保证 Redis
Cluster 下 ownership key 与 lease key 同 slot，Lua 多 key 访问合法）：

- ownership: ``{<ns>}:run:owner:<run_id>`` (String, value = ``worker:lease``)
- lease:     ``{<ns>}:lease:data:<worker_id>`` (Hash, 由 LeaseStore 维护)

历史键 ``<ns>:run:owner:*``（无 hash tag）已废弃，不做迁移。注意
ownership TTL 并不短（默认 = lease TTL + margin，约 65 分钟）：滚动
升级到本布局期间，旧进程写的旧键对新进程不可见，互斥在窗口内退化为
"新键从空开始"。升级前先 drain（停止派发新 run 并等在途 run 结束）
可完全消除该窗口；直接滚动升级则接受旧键在 TTL 内自然过期。
"""

from __future__ import annotations

from collections.abc import Awaitable
from enum import Enum
from typing import Any, cast

from antcode_core.application.services.lease_service import (
    LEASE_RECORD_RETENTION_MS,
    LeaseStore,
)
from antcode_core.application.services.workers.run_ownership_fence_lua import (
    _CLAIM_SCRIPT,
    _RELEASE_SCRIPT,
    _RENEW_SCRIPT,
    _TAKEOVER_SCRIPT,
)
from antcode_core.infrastructure.redis import redis_namespace

RUN_OWNER_KEY_TEMPLATE = "{{{ns}}}:run:owner:{run_id}"

_RESULT_ACQUIRED = 1
_RESULT_HELD_BY_OTHER = 0
_RESULT_LEASE_STALE = -1


class OwnershipOutcome(Enum):
    """单次 ownership 操作的原子判定结果。"""

    ACQUIRED = "acquired"
    HELD_BY_OTHER = "held_by_other"
    LEASE_STALE = "lease_stale"


def run_owner_key(run_id: str, namespace: str | None = None) -> str:
    """Ownership key，与 Lease key 共用 ``{<ns>}`` hash tag（同 slot）。"""
    return RUN_OWNER_KEY_TEMPLATE.format(ns=redis_namespace(namespace), run_id=run_id)


def ownership_token(worker_id: str, lease_id: str) -> str:
    return f"{worker_id}:{lease_id}"


def _lease_key(worker_id: str, namespace: str | None) -> str:
    return LeaseStore.LEASE_KEY_TEMPLATE.format(ns=redis_namespace(namespace), worker_id=worker_id)


def _to_outcome(result: Any) -> OwnershipOutcome:
    value = int(result or 0)
    if value == _RESULT_ACQUIRED:
        return OwnershipOutcome.ACQUIRED
    if value == _RESULT_LEASE_STALE:
        return OwnershipOutcome.LEASE_STALE
    if value == _RESULT_HELD_BY_OTHER:
        return OwnershipOutcome.HELD_BY_OTHER
    raise RuntimeError(f"run ownership Lua 返回未知结果: {result!r}")


async def _run_fenced_script(
    redis: Any,
    script: str,
    *,
    worker_id: str,
    lease_id: str,
    run_id: str,
    ttl_ms: int,
    namespace: str | None,
) -> OwnershipOutcome:
    result = await cast(
        "Awaitable[Any]",
        redis.eval(
            script,
            2,
            run_owner_key(run_id, namespace),
            _lease_key(worker_id, namespace),
            ownership_token(worker_id, lease_id),
            str(int(ttl_ms)),
            worker_id,
            lease_id,
            str(LEASE_RECORD_RETENTION_MS),
        ),
    )
    return _to_outcome(result)


async def claim_run_ownership(
    redis: Any,
    *,
    worker_id: str,
    lease_id: str,
    run_id: str,
    ttl_ms: int,
    namespace: str | None = None,
) -> OwnershipOutcome:
    """原子 claim：lease 校验 + SET NX/续期 + 同 worker 旧代际接管。

    复审 P1-DR-02: HELD_BY_OTHER 时追加一次死主接管尝试——holder 的
    权威 Lease 已换代/过期（其崩溃或失联）则原子接管，不再等最长
    65 分钟的 ownership TTL。holder 仍存活时维持 HELD_BY_OTHER。
    """
    outcome = await _run_fenced_script(
        redis,
        _CLAIM_SCRIPT,
        worker_id=worker_id,
        lease_id=lease_id,
        run_id=run_id,
        ttl_ms=ttl_ms,
        namespace=namespace,
    )
    if outcome is not OwnershipOutcome.HELD_BY_OTHER:
        return outcome
    return await _attempt_dead_holder_takeover(
        redis,
        worker_id=worker_id,
        lease_id=lease_id,
        run_id=run_id,
        ttl_ms=ttl_ms,
        namespace=namespace,
    )


def parse_ownership_token(raw: Any) -> tuple[str, str] | None:
    """owner key 的 value（``worker:lease``）→ ``(worker_id, lease_id)``。

    按最后一个 ``:`` 切分，因此 ``ownership_token(*parse_ownership_token(v))``
    与原值逐字相等（worker_id 含 ``:`` 时也成立）。无法解析返回 ``None``，
    由调用方决定如何处理——本函数不猜测。
    """
    holder = raw.decode() if isinstance(raw, bytes) else raw
    if not holder:
        return None
    token = str(holder)
    if ":" not in token:
        return None
    holder_worker, holder_lease = token.rsplit(":", 1)
    if not holder_worker or not holder_lease:
        return None
    return holder_worker, holder_lease


async def _attempt_dead_holder_takeover(
    redis: Any,
    *,
    worker_id: str,
    lease_id: str,
    run_id: str,
    ttl_ms: int,
    namespace: str | None,
) -> OwnershipOutcome:
    owner_key = run_owner_key(run_id, namespace)
    raw = await cast("Awaitable[Any]", redis.get(owner_key))
    parsed = parse_ownership_token(raw)
    if parsed is None:
        # holder 已消失（TTL 到期/释放）：直接重试普通 claim。
        return await _run_fenced_script(
            redis,
            _CLAIM_SCRIPT,
            worker_id=worker_id,
            lease_id=lease_id,
            run_id=run_id,
            ttl_ms=ttl_ms,
            namespace=namespace,
        )
    holder_worker_id, holder_lease_id = parsed
    holder_token = ownership_token(holder_worker_id, holder_lease_id)
    result = await cast(
        "Awaitable[Any]",
        redis.eval(
            _TAKEOVER_SCRIPT,
            3,
            owner_key,
            _lease_key(worker_id, namespace),
            _lease_key(holder_worker_id, namespace),
            ownership_token(worker_id, lease_id),
            str(int(ttl_ms)),
            worker_id,
            lease_id,
            str(LEASE_RECORD_RETENTION_MS),
            holder_token,
            holder_lease_id,
        ),
    )
    return _to_outcome(result)


async def renew_run_ownership(
    redis: Any,
    *,
    worker_id: str,
    lease_id: str,
    run_id: str,
    ttl_ms: int,
    namespace: str | None = None,
) -> OwnershipOutcome:
    """原子 renew：lease 校验 + token 匹配才 PEXPIRE。"""
    return await _run_fenced_script(
        redis,
        _RENEW_SCRIPT,
        worker_id=worker_id,
        lease_id=lease_id,
        run_id=run_id,
        ttl_ms=ttl_ms,
        namespace=namespace,
    )


async def release_run_ownership(
    redis: Any,
    *,
    worker_id: str,
    lease_id: str,
    run_id: str,
    namespace: str | None = None,
) -> bool:
    """token 精确匹配才 DEL；key 不存在视为已释放。"""
    result = await cast(
        "Awaitable[Any]",
        redis.eval(
            _RELEASE_SCRIPT,
            1,
            run_owner_key(run_id, namespace),
            ownership_token(worker_id, lease_id),
        ),
    )
    return int(result or 0) == 1


__all__ = [
    "OwnershipOutcome",
    "claim_run_ownership",
    "ownership_token",
    "parse_ownership_token",
    "release_run_ownership",
    "renew_run_ownership",
    "run_owner_key",
]
