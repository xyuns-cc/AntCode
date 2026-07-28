"""Authenticated Gateway RPCs for Redis-backed run execution fencing.

P1-GW-04 修复：claim/renew 不再依赖 RPC 入口的 check-then-act 校验链，
而是调用 ``run_ownership_fence`` 的 Lua 脚本，在与写入同一个原子步骤里
比对权威 Lease Hash。旧代际（L1）即使通过了入口校验，脚本执行时其
lease_id 已不是当前代际，返回 LEASE_STALE 并 abort，不可能再创建或
续期 ownership 阻塞新代际（L2）。

复审 P1-GW-02: PG 的 ``TaskRun.lease_id`` 绑定顺序修正为 **fence 先行**。
此前先绑定 PG 再跑 fence：切代命中 LEASE_STALE 时 PG 已停在旧代际，
新代际（绑定 CAS 只允许 NULL→X）被永久拒绝。现在 claim 流程是：
worker 归属预检（不绑定）→ fence Lua（代际权威判定）→ ACQUIRED 后
才把 PG 改绑到当前代际（允许同 worker 换代改绑）。
"""

from __future__ import annotations

from typing import Any

import grpc
from antcode_contracts import artifact_pb2
from antcode_core.application.services.workers.log_ingest_generation import (
    read_log_ingest_cutoff,
)
from antcode_core.application.services.workers.run_ownership_fence import (
    OwnershipOutcome,
    claim_run_ownership,
    release_run_ownership,
    renew_run_ownership,
)
from antcode_core.application.services.workers.run_ownership_service import (
    bind_worker_run_lease_generation,
    require_worker_owns_runs,
    require_worker_owns_runs_for_lease,
)
from antcode_core.infrastructure.redis import get_redis_client
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from loguru import logger

from antcode_gateway.auth import require_authenticated_worker
from antcode_gateway.services.run_ownership_contract import (
    MAX_RUN_OWNERSHIP_TTL_MS,
    validate_ownership_fields,
)
from antcode_gateway.services.run_ownership_contract import (
    OwnershipBindError as _OwnershipBindError,
)
from antcode_gateway.services.run_ownership_contract import (
    RunOwnershipIdentity as _RunOwnershipIdentity,
)


class RunOwnershipRpcMixin:
    """ArtifactService methods that keep Gateway Workers away from Redis."""

    _lease_verifier: Any

    async def ClaimRunOwnership(self, request, context):
        identity = await self._authorize_ownership_request(
            request,
            context,
            require_ttl=True,
            require_lease_binding=False,
        )
        if identity is None:
            return artifact_pb2.RunOwnershipClaimResponse(acquired=False)
        acquired = await self._run_fenced_operation(context, claim_run_ownership, identity)
        # P1-GW-02: 只有 fence 证明当前代际并取得 ownership 后才落 PG 绑定。
        if acquired:
            try:
                await self._bind_lease_generation(identity)
            except _OwnershipBindError as exc:
                await self._run_release_operation(context, identity)
                await context.abort(exc.code, exc.detail)
                return artifact_pb2.RunOwnershipClaimResponse(acquired=False)
        return artifact_pb2.RunOwnershipClaimResponse(acquired=acquired)

    async def RenewRunOwnership(self, request, context):
        identity = await self._authorize_ownership_request(
            request,
            context,
            require_ttl=True,
            require_lease_binding=True,
        )
        if identity is None:
            return artifact_pb2.RunOwnershipRenewResponse(renewed=False)
        renewed = await self._run_fenced_operation(context, renew_run_ownership, identity)
        return artifact_pb2.RunOwnershipRenewResponse(renewed=renewed)

    async def ReleaseRunOwnership(self, request, context):
        identity = await self._authorize_ownership_request(
            request,
            context,
            require_ttl=False,
            require_lease_binding=True,
        )
        if identity is None:
            return artifact_pb2.RunOwnershipReleaseResponse(released=False)
        released = await self._run_release_operation(context, identity)
        return artifact_pb2.RunOwnershipReleaseResponse(released=released)

    async def _authorize_ownership_request(
        self,
        request: Any,
        context: grpc.aio.ServicerContext,
        *,
        require_ttl: bool,
        require_lease_binding: bool,
    ) -> _RunOwnershipIdentity | None:
        worker_id = await require_authenticated_worker(context, request.worker_id)
        if not worker_id:
            return None
        identity = await validate_ownership_fields(request, context, worker_id, require_ttl=require_ttl)
        if identity is None:
            return None
        # 入口 Lease 预检仅用于尽早拒绝 + 精确错误信息；真正的代际权威
        # 判定在 fence Lua 内与写入原子完成（见 _run_fenced_operation）。
        if not await self._require_current_ownership_lease(identity, context):
            return None
        if not await self._require_task_run_ownership(
            identity,
            context,
            require_lease_binding=require_lease_binding,
        ):
            return None
        return identity

    async def _require_current_ownership_lease(
        self,
        identity: _RunOwnershipIdentity,
        context: grpc.aio.ServicerContext,
    ) -> bool:
        try:
            current = await self._lease_verifier(identity.worker_id, identity.lease_id)
        except Exception:
            logger.exception("run ownership Lease 校验失败")
            await context.abort(grpc.StatusCode.UNAVAILABLE, "run ownership Lease 校验失败")
            return False
        if not current:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "lease_id 不是当前 Worker Lease")
            return False
        return True

    @staticmethod
    async def _require_task_run_ownership(
        identity: _RunOwnershipIdentity,
        context: grpc.aio.ServicerContext,
        *,
        require_lease_binding: bool,
    ) -> bool:
        try:
            if require_lease_binding:
                # renew/release：run 必须已绑定到请求代际（claim 时落库）。
                await require_worker_owns_runs_for_lease(
                    identity.worker_id,
                    [identity.run_id],
                    lease_id=identity.lease_id,
                )
            else:
                # claim 预检只验 worker 归属，**不绑定**：绑定必须等 fence
                # 证明代际后进行（P1-GW-02）。
                await require_worker_owns_runs(identity.worker_id, [identity.run_id])
            return True
        except ValueError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except PermissionError as exc:
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, str(exc))
        except Exception:
            logger.exception("run ownership TaskRun 校验失败")
            await context.abort(grpc.StatusCode.UNAVAILABLE, "run ownership TaskRun 校验失败")
        return False

    @staticmethod
    async def _bind_lease_generation(
        identity: _RunOwnershipIdentity,
    ) -> None:
        """fence ACQUIRED 之后把 PG 绑定改到当前代际(允许同 worker 换代)。

        P1-GW-04 (round4 修正):lease_gen 必须用 **lease 授予时刻**
        (Redis Hash granted_at_ms),不能用 fence 后 bind 时的 time.time()。
        原实现的 bug 场景:L1 fence(T=110) → asyncio 调度切走暂停到 T=250,
        L2 在 T=210 fence+bind,PG.lease_gen=210, lease_id=lease-2;L1 T=250
        醒来 bind 用 time.time()=250,CAS `stored(210) <= NEW(250)`? true
        → 覆盖 L2。
        正确:用 granted_at_ms 作为 gen(L1 lease 更早授予,granted<L2),CAS
        `stored(200) <= NEW(100)`? false → 拒绝 L1 迟到覆盖 ✓。
        """
        from antcode_core.application.services.lease_service import LeasePolicy, LeaseStore

        try:
            redis = await get_redis_client()
            if redis is None:
                raise RuntimeError("Redis unavailable")
            store = LeaseStore(redis, namespace=redis_namespace(), policy=LeasePolicy())
            lease = await store.get(identity.worker_id, include_expired=False)
            if lease is None or lease.lease_id != identity.lease_id:
                raise _OwnershipBindError(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    f"lease 已失效或换代(fence 后被撤销): run_id={identity.run_id}",
                )
            # P1-GW-01 (round6): 优先用 sequence 作 gen(严格单调,同毫秒无碰撞);
            # 存量 lease 无 sequence 字段时 (=0) 回退到 granted_at_ms 保持兼容。
            # sequence 存在时 gen 一定 > 0, granted_at_ms 场景与它不会混用。
            lease_gen = int(lease.sequence) if lease.sequence > 0 else int(lease.granted_at_ms)
            log_cutoff_id = await read_log_ingest_cutoff(redis, namespace=redis_namespace())
            await bind_worker_run_lease_generation(
                identity.worker_id,
                identity.run_id,
                lease_id=identity.lease_id,
                lease_gen=lease_gen,
                log_cutoff_id=log_cutoff_id,
            )
            outcome = await renew_run_ownership(
                redis,
                worker_id=identity.worker_id,
                lease_id=identity.lease_id,
                run_id=identity.run_id,
                ttl_ms=int(identity.ttl_ms or 0),
                namespace=redis_namespace(),
            )
            if outcome is not OwnershipOutcome.ACQUIRED:
                raise _OwnershipBindError(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    f"lease 在 ownership bind 后已失效或换代: run_id={identity.run_id}",
                )
            return
        except _OwnershipBindError:
            raise
        except PermissionError as exc:
            raise _OwnershipBindError(grpc.StatusCode.PERMISSION_DENIED, str(exc)) from exc
        except Exception as exc:
            logger.exception("run ownership Lease 绑定失败: run_id={}", identity.run_id)
            raise _OwnershipBindError(
                grpc.StatusCode.UNAVAILABLE,
                "run ownership lease binding unavailable",
            ) from exc

    @staticmethod
    async def _run_fenced_operation(
        context: grpc.aio.ServicerContext,
        operation: Any,
        identity: _RunOwnershipIdentity,
    ) -> bool:
        try:
            redis = await get_redis_client()
            if redis is None:
                raise RuntimeError("Redis unavailable")
            outcome = await operation(
                redis,
                worker_id=identity.worker_id,
                lease_id=identity.lease_id,
                run_id=identity.run_id,
                ttl_ms=int(identity.ttl_ms or 0),
            )
        except Exception:
            logger.exception("run ownership Redis 操作失败: run_id={}", identity.run_id)
            await context.abort(grpc.StatusCode.UNAVAILABLE, "run ownership persistence unavailable")
            return False
        if outcome is OwnershipOutcome.LEASE_STALE:
            # 入口预检通过后代际被切换（TOCTOU 命中）：显式拒绝，worker
            # 必须放弃执行并重新走注册/Lease 流程。
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "lease generation superseded during ownership operation",
            )
            return False
        return outcome is OwnershipOutcome.ACQUIRED

    @staticmethod
    async def _run_release_operation(
        context: grpc.aio.ServicerContext,
        identity: _RunOwnershipIdentity,
    ) -> bool:
        try:
            redis = await get_redis_client()
            if redis is None:
                raise RuntimeError("Redis unavailable")
            return await release_run_ownership(
                redis,
                worker_id=identity.worker_id,
                lease_id=identity.lease_id,
                run_id=identity.run_id,
            )
        except Exception:
            logger.exception("run ownership Redis 操作失败: run_id={}", identity.run_id)
            await context.abort(grpc.StatusCode.UNAVAILABLE, "run ownership persistence unavailable")
            return False


__all__ = ["MAX_RUN_OWNERSHIP_TTL_MS", "RunOwnershipRpcMixin"]
