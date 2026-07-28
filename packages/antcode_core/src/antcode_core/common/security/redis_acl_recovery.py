"""Explicit compensation helpers for non-transactional Redis ACL changes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from antcode_core.common.security.secret_box import secret_box


@dataclass(frozen=True)
class AclSnapshot:
    username: str | None
    encrypted_password: str | None
    plaintext_password: str | None
    revision: int
    synced_at: Any


def snapshot_acl(worker: Any) -> AclSnapshot:
    encrypted = getattr(worker, "redis_password_encrypted", None)
    return AclSnapshot(
        username=getattr(worker, "redis_username", None),
        encrypted_password=encrypted,
        plaintext_password=secret_box.decrypt(encrypted) if encrypted else None,
        revision=getattr(worker, "redis_acl_revision", 0) or 0,
        synced_at=getattr(worker, "redis_acl_synced_at", None),
    )


def restore_acl_fields(worker: Any, snapshot: AclSnapshot) -> None:
    worker.redis_username = snapshot.username
    worker.redis_password_encrypted = snapshot.encrypted_password
    worker.redis_acl_revision = snapshot.revision
    worker.redis_acl_synced_at = snapshot.synced_at


async def rollback_or_raise(
    redis_admin_client: Any,
    worker: Any,
    *,
    snapshot: AclSnapshot,
    current_username: str | None,
    original_error: BaseException,
    build_setuser_args: Callable[..., list[str]],
    namespace: str,
) -> None:
    try:
        await _restore_redis_acl(
            redis_admin_client,
            worker.public_id,
            snapshot=snapshot,
            current_username=current_username,
            build_setuser_args=build_setuser_args,
            namespace=namespace,
        )
    except BaseException as rollback_error:
        raise BaseExceptionGroup(
            "Redis ACL 操作失败且补偿失败",
            [original_error, rollback_error],
        ) from original_error


async def _restore_redis_acl(
    redis_admin_client: Any,
    worker_public_id: str,
    *,
    snapshot: AclSnapshot,
    current_username: str | None,
    build_setuser_args: Callable[..., list[str]],
    namespace: str,
) -> None:
    if snapshot.username and snapshot.plaintext_password:
        args = build_setuser_args(
            snapshot.username,
            snapshot.plaintext_password,
            worker_public_id,
            namespace=namespace,
        )
        await redis_admin_client.execute_command("ACL", *args)
    elif current_username:
        await redis_admin_client.execute_command("ACL", "DELUSER", current_username)
    else:
        raise RuntimeError("Redis ACL 补偿缺少当前用户名")
    await redis_admin_client.execute_command("ACL", "SAVE")


__all__ = ["AclSnapshot", "restore_acl_fields", "rollback_or_raise", "snapshot_acl"]
