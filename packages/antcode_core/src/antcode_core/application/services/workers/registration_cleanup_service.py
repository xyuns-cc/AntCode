"""Cleanup expired, unacknowledged Worker registrations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tortoise.transactions import in_transaction

from antcode_core.domain.models import WorkerInstallKey

REGISTRATION_CLEANUP_BATCH_SIZE = 100
ProvisionalRunSettler = Callable[[int], Awaitable[int]]


@dataclass(frozen=True)
class RegistrationCleanupResult:
    expired_registrations: int = 0
    deleted_workers: int = 0
    expired_pending_keys: int = 0


class RegistrationCleanupService:
    async def cleanup_expired(self, *, run_settler: ProvisionalRunSettler) -> RegistrationCleanupResult:
        """Expire registration state, then retire its provisional Worker.

        The injected settler is deliberately mandatory: only Master can prove the
        current scheduler epoch before terminally settling active TaskRun rows.
        """
        now = datetime.now(UTC)
        expired_registrations = 0
        deleted_workers = 0
        while True:
            batch = await self._cleanup_batch(now, run_settler)
            expired_registrations += batch.expired_registrations
            deleted_workers += batch.deleted_workers
            if batch.expired_registrations < REGISTRATION_CLEANUP_BATCH_SIZE:
                break
        # 复审 F4-A: 上一轮 "标 expired 后、删 Worker 前" 崩溃或删除失败的
        # 遗留 —— expired 且未确认但仍绑着存活 Worker 的记录，每轮补扫重试。
        deleted_workers += await self._retry_orphaned_expired_workers(run_settler)
        expired_pending_keys = await self._expire_pending_keys(now)
        return RegistrationCleanupResult(expired_registrations, deleted_workers, expired_pending_keys)

    @staticmethod
    async def _expire_pending_keys(now: datetime) -> int:
        expired = 0
        while True:
            batch_size = await WorkerInstallKey.expire_pending(now, limit=REGISTRATION_CLEANUP_BATCH_SIZE)
            expired += batch_size
            if batch_size < REGISTRATION_CLEANUP_BATCH_SIZE:
                return expired

    async def _cleanup_batch(
        self,
        now: datetime,
        run_settler: ProvisionalRunSettler,
    ) -> RegistrationCleanupResult:
        async with in_transaction("default") as connection:
            registrations = await self._expired_registrations(now, connection)
            if not registrations:
                return RegistrationCleanupResult()
            # 批内行已被 select_for_update 锁定，谓词在事务内不会失配；
            # 用一条批量 UPDATE 取代逐行语句，缩短持锁时长。
            expired = await (
                WorkerInstallKey.filter(
                    id__in=[registration.id for registration in registrations],
                    status="used",
                    recovery_expires_at__lte=now,
                    registration_acknowledged_at__isnull=True,
                )
                .using_db(connection)
                .update(
                    status="expired",
                    recovery_secret_hash=None,
                    registration_request_hash=None,
                    recovery_expires_at=None,
                )
            )
            worker_ids = [registration.used_by_worker for registration in registrations if registration.used_by_worker]
        # 复审 F4-A: 保留 used_by_worker 绑定 —— 它是崩溃/删除失败后补扫
        # 重试的唯一线索；worker_service 级联删除对 status="expired" 的
        # 安装 Key 做了豁免（审计留存 + acknowledge 精确报"恢复窗口已关闭"）。
        deleted = await self._delete_workers_via_service(worker_ids, run_settler)
        return RegistrationCleanupResult(expired, deleted)

    async def _retry_orphaned_expired_workers(self, run_settler: ProvisionalRunSettler) -> int:
        from antcode_core.domain.models import Worker

        orphaned = (
            await WorkerInstallKey.filter(
                status="expired",
                registration_acknowledged_at__isnull=True,
                used_by_worker__not_isnull=True,
            )
            .only("id", "used_by_worker")
            .limit(REGISTRATION_CLEANUP_BATCH_SIZE)
            .all()
        )
        if not orphaned:
            return 0
        deleted = 0
        for key in orphaned:
            worker_exists = await Worker.filter(public_id=key.used_by_worker).exists()
            if worker_exists:
                deleted += await self._delete_workers_via_service([key.used_by_worker], run_settler)
                continue
            # Worker 已删净：解除绑定，让补扫清单收敛（Key 仍以 expired 留存）。
            await WorkerInstallKey.filter(id=key.id).update(used_by_worker=None)
        return deleted

    @staticmethod
    async def _delete_workers_via_service(
        worker_ids: list[str],
        run_settler: ProvisionalRunSettler,
    ) -> int:
        """P1-DB-05: 过期注册的 Worker 必须走完整撤销链删除。

        此前直接批量 ``Worker.delete``，绕过 ``worker_service``
        的 Redis ACL revoke、心跳/权限/runtime/项目任务归属级联 —— 未 ACK
        Worker 的 ACL 凭证与逻辑关系会永久残留。临时 Worker 的活跃 run
        必须由 Master 当前调度任期显式结算，随后才进入通用级联删除；逐个
        失败只记录，留待下轮重试。
        """
        if not worker_ids:
            return 0
        from loguru import logger

        from antcode_core.application.services.workers.worker_service import worker_service

        deleted = 0
        for worker_public_id in worker_ids:
            try:
                if await worker_service.delete_expired_provisional_worker(worker_public_id, run_settler):
                    deleted += 1
            except Exception:
                logger.exception("过期注册 Worker 完整撤销失败(下轮重试): worker={}", worker_public_id)
        return deleted

    @staticmethod
    async def _expired_registrations(now: datetime, connection: Any) -> list[WorkerInstallKey]:
        return await (
            WorkerInstallKey.filter(
                status="used",
                registration_id__isnull=False,
                recovery_secret_hash__isnull=False,
                recovery_expires_at__lte=now,
                registration_acknowledged_at__isnull=True,
            )
            .using_db(connection)
            .select_for_update(skip_locked=True)
            .order_by("id")
            .limit(REGISTRATION_CLEANUP_BATCH_SIZE)
        )


registration_cleanup_service = RegistrationCleanupService()

__all__ = [
    "RegistrationCleanupResult",
    "RegistrationCleanupService",
    "registration_cleanup_service",
]
