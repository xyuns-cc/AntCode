"""Worker 权限持久化服务。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from loguru import logger
from tortoise.transactions import in_transaction

from antcode_core.domain.models import User, UserWorkerPermission, Worker
from antcode_core.domain.models.enums import WorkerPermission


class WorkerPermissionTargetNotFound(ValueError):
    """权限操作目标不存在。"""


class AdminWorkerPermissionError(ValueError):
    """管理员无需显式 Worker 权限。"""


def _normalize_permission(permission: WorkerPermission | str) -> WorkerPermission:
    try:
        return WorkerPermission(permission)
    except ValueError as exc:
        raise ValueError("Worker 权限仅支持 view 或 use") from exc


async def _lock_user(connection, user_id: int) -> User:
    user = await User.filter(id=user_id).using_db(connection).select_for_update().only("id", "is_admin").first()
    if user is None:
        raise WorkerPermissionTargetNotFound("用户不存在")
    if user.is_admin:
        raise AdminWorkerPermissionError("管理员默认拥有全部 Worker 权限，无需分配")
    return user


async def _lock_workers(connection, worker_ids: list[int]) -> list[Worker]:
    workers = (
        await Worker.filter(id__in=worker_ids)
        .using_db(connection)
        .order_by("id")
        .select_for_update()
        .only("id", "name")
        .all()
    )
    found_ids = {worker.id for worker in workers}
    missing_ids = sorted(set(worker_ids) - found_ids)
    if missing_ids:
        raise WorkerPermissionTargetNotFound(f"Worker 不存在: {', '.join(map(str, missing_ids))}")
    return workers


class WorkerPermissionService:
    """管理用户和 Worker 之间的显式权限。"""

    async def assign(
        self,
        *,
        worker_id: int,
        user_id: int,
        permission: WorkerPermission | str,
        assigned_by: int | None,
        note: str | None,
    ) -> None:
        normalized = _normalize_permission(permission)
        async with in_transaction("default") as connection:
            await _lock_user(connection, user_id)
            workers = await _lock_workers(connection, [worker_id])
            existing = await self._lock_existing(connection, user_id, worker_id)
            if existing is None:
                await UserWorkerPermission.create(
                    user_id=user_id,
                    worker_id=worker_id,
                    permission=normalized.value,
                    assigned_by=assigned_by,
                    note=note,
                    using_db=connection,
                )
            else:
                existing.permission = normalized.value
                existing.assigned_by = assigned_by
                existing.note = note
                await existing.save(using_db=connection, update_fields=("permission", "assigned_by", "note"))
        logger.info(f"分配 Worker {workers[0].name} 给用户 {user_id}, 权限: {normalized.value}")

    async def revoke(self, *, worker_id: int, user_id: int) -> bool:
        async with in_transaction("default") as connection:
            await _lock_user(connection, user_id)
            await _lock_workers(connection, [worker_id])
            deleted = (
                await UserWorkerPermission.filter(user_id=user_id, worker_id=worker_id).using_db(connection).delete()
            )
        if deleted:
            logger.info(f"撤销用户 {user_id} 的 Worker {worker_id} 权限")
        return deleted > 0

    async def batch_assign(
        self,
        *,
        user_id: int,
        worker_ids: list[int],
        permission: WorkerPermission | str,
        assigned_by: int | None,
    ) -> dict[str, int]:
        normalized = _normalize_permission(permission)
        unique_ids = list(dict.fromkeys(worker_ids))
        if not unique_ids or len(unique_ids) != len(worker_ids):
            raise ValueError("worker_ids 不能为空且不能重复")
        async with in_transaction("default") as connection:
            await _lock_user(connection, user_id)
            await _lock_workers(connection, unique_ids)
            existing_ids = await self._existing_ids(connection, user_id, unique_ids)
            new_ids = [worker_id for worker_id in unique_ids if worker_id not in existing_ids]
            permissions = self._new_permissions(
                user_id=user_id,
                worker_ids=new_ids,
                permission=normalized,
                assigned_by=assigned_by,
            )
            if permissions:
                await UserWorkerPermission.bulk_create(permissions, using_db=connection)
        logger.info(f"批量分配 Worker 权限: 用户{user_id}, 新增{len(permissions)}个")
        return {"success": len(permissions), "failed": 0, "skipped": len(existing_ids)}

    async def get_worker_users(self, worker_id: int) -> list[dict]:
        permissions = await UserWorkerPermission.filter(worker_id=worker_id).all()
        users = await self._permission_users(permissions)
        return [
            self._user_permission_response(perm, users[perm.user_id]) for perm in permissions if perm.user_id in users
        ]

    @staticmethod
    async def _lock_existing(connection, user_id: int, worker_id: int):
        return (
            await UserWorkerPermission.filter(user_id=user_id, worker_id=worker_id)
            .using_db(connection)
            .select_for_update()
            .first()
        )

    @staticmethod
    async def _existing_ids(connection, user_id: int, worker_ids: list[int]) -> set[int]:
        values = (
            await UserWorkerPermission.filter(user_id=user_id, worker_id__in=worker_ids)
            .using_db(connection)
            .values_list("worker_id", flat=True)
        )
        return set(cast(list[int], values))

    @staticmethod
    def _new_permissions(
        *,
        user_id: int,
        worker_ids: list[int],
        permission: WorkerPermission,
        assigned_by: int | None,
    ) -> list[UserWorkerPermission]:
        assigned_at = datetime.now(UTC)
        return [
            UserWorkerPermission(
                user_id=user_id,
                worker_id=worker_id,
                permission=permission.value,
                assigned_by=assigned_by,
                assigned_at=assigned_at,
            )
            for worker_id in worker_ids
        ]

    @staticmethod
    async def _permission_users(permissions) -> dict[int, User]:
        user_ids = [permission.user_id for permission in permissions]
        users = await User.filter(id__in=user_ids, is_admin=False).all() if user_ids else []
        return {user.id: user for user in users}

    @staticmethod
    def _user_permission_response(permission, user: User) -> dict:
        return {
            "user_id": user.public_id,
            "username": user.username,
            "permission": permission.permission,
            "assigned_at": permission.assigned_at.isoformat() if permission.assigned_at else None,
            "note": permission.note,
        }


worker_permission_service = WorkerPermissionService()

__all__ = [
    "AdminWorkerPermissionError",
    "WorkerPermissionService",
    "WorkerPermissionTargetNotFound",
    "worker_permission_service",
]
