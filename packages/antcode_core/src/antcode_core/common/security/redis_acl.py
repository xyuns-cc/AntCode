"""Direct Worker 独立 Redis ACL 凭证管理。

给每个 Direct Worker 发一个 ``worker_<public_id>`` 账户，
通过 ACL 规则限制其只能访问：
- 自己的 per-worker key（task:ready:<id>、heartbeat:<id> 等）
- 必要的共享 stream（task:result、log:ingest、控制全局流等）

防止单 worker 被攻破后横向读写其他 worker 的数据。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from antcode_core.common.config import settings
from antcode_core.common.security.api_key import generate_api_key
from antcode_core.common.security.secret_box import secret_box

# Worker 仅能读写自己 worker_id 的 key
PER_WORKER_KEY_PATTERNS: list[str] = [
    "antcode:task:ready:{wid}",
    "antcode:task:ready:{wid}:*",
    "antcode:task:pending:{wid}",
    "antcode:task:pending:{wid}:*",
    "antcode:control:{wid}",
    "antcode:control:{wid}:*",
    "antcode:control:reply:{wid}:*",
    "antcode:worker:info:{wid}",
    "antcode:worker:state:{wid}",
    "antcode:heartbeat:{wid}",
]

# 所有 Worker 都需要访问的共享 key（接受 worker 互相能写日志/结果，
# 但 task:ready 等任务拉取流仍按 worker 隔离）
SHARED_KEY_PATTERNS: list[str] = [
    "antcode:task:result",
    "antcode:task:result:*",
    "antcode:log:ingest",
    "antcode:log:ingest:*",
    "antcode:log:stream:*",
    "antcode:log:chunk:*",
    "antcode:spider:data:*",
    "antcode:spider:meta:*",
    "antcode:control:global",
    "antcode:heartbeat:active",
    "antcode:worker:all",
]


def _acl_username(worker_public_id: str) -> str:
    return f"worker_{worker_public_id}"


def _build_setuser_args(username: str, password: str, worker_public_id: str) -> list[str]:
    """组装 ACL SETUSER 命令的参数列表。"""
    args: list[str] = [
        "SETUSER",
        username,
        "on",
        f">{password}",
        "resetkeys",
    ]
    for pat in PER_WORKER_KEY_PATTERNS:
        args.append(f"~{pat.format(wid=worker_public_id)}")
    for pat in SHARED_KEY_PATTERNS:
        args.append(f"~{pat}")
    # Channel pattern（pub/sub），目前未使用但留通配 antcode:*
    args.append("&antcode:*")
    # 命令权限：基础数据结构 OK；禁掉危险命令防提权/全库扫描
    args.extend(
        [
            "+@read",
            "+@write",
            "+@stream",
            "+@hash",
            "+@set",
            "+@string",
            "+@scripting",
            "+@connection",
            "-@dangerous",
            "-@admin",
            "-flushall",
            "-flushdb",
            "-keys",
            "-acl",
            "-config",
        ]
    )
    return args


async def ensure_worker_acl(
    redis_admin_client,
    worker,
    *,
    new_password: str | None = None,
) -> str:
    """建立或刷新 Worker 的 Redis ACL，返回明文密码（仅此一次）。

    - ``new_password=None`` 时自动生成新密码（用 R5 的 generate_api_key）
    - 总是执行 ACL SETUSER + ACL SAVE
    - 更新 worker 表的 redis_username/encrypted password/revision/synced_at
    """
    if not settings.REDIS_ACL_ENABLED:
        raise RuntimeError("REDIS_ACL_ENABLED=false 时不应调用 ensure_worker_acl")

    plaintext_password = new_password or generate_api_key(prefix="rk", length=32)
    username = _acl_username(worker.public_id)

    args = _build_setuser_args(username, plaintext_password, worker.public_id)
    await redis_admin_client.execute_command("ACL", *args)
    try:
        await redis_admin_client.execute_command("ACL", "SAVE")
    except Exception as exc:  # SAVE 不影响 SETUSER 已生效
        logger.warning("ACL SAVE 失败（SETUSER 已生效）: {}", exc)

    worker.redis_username = username
    worker.redis_password_encrypted = secret_box.encrypt(plaintext_password)
    worker.redis_acl_revision = (worker.redis_acl_revision or 0) + 1
    worker.redis_acl_synced_at = datetime.now(UTC)
    await worker.save(
        update_fields=[
            "redis_username",
            "redis_password_encrypted",
            "redis_acl_revision",
            "redis_acl_synced_at",
        ]
    )
    logger.info(
        "Worker {} Redis ACL 已签发: user={} revision={}",
        worker.public_id,
        username,
        worker.redis_acl_revision,
    )
    return plaintext_password


async def revoke_worker_acl(redis_admin_client, worker) -> None:
    """删除 Worker 的 Redis ACL（worker 注销时调用）。"""
    if not worker.redis_username:
        return
    try:
        await redis_admin_client.execute_command("ACL", "DELUSER", worker.redis_username)
        await redis_admin_client.execute_command("ACL", "SAVE")
    except Exception as exc:
        logger.warning("ACL DELUSER {} 失败: {}", worker.redis_username, exc)
    worker.redis_username = None
    worker.redis_password_encrypted = None
    worker.redis_acl_synced_at = None
    await worker.save(update_fields=["redis_username", "redis_password_encrypted", "redis_acl_synced_at"])


async def sync_all_worker_acls(redis_admin_client) -> dict[str, Any]:
    """Master 启动时把 PG 中已有 ACL 凭证的 worker 全量推到 Redis。

    用于 Redis 重启 / 跨环境迁移时恢复。worker 表才是单一权威。
    """
    from antcode_core.domain.models.worker import Worker

    if not settings.REDIS_ACL_ENABLED:
        return {"skipped": True, "reason": "REDIS_ACL_ENABLED=false"}

    synced = 0
    failed: list[str] = []
    async for worker in Worker.filter(redis_username__not_isnull=True):
        try:
            plaintext = secret_box.decrypt(worker.redis_password_encrypted)
            args = _build_setuser_args(worker.redis_username, plaintext, worker.public_id)
            await redis_admin_client.execute_command("ACL", *args)
            synced += 1
        except Exception as exc:
            logger.warning("同步 ACL 失败 worker={} err={}", worker.public_id, exc)
            failed.append(worker.public_id)
    try:
        await redis_admin_client.execute_command("ACL", "SAVE")
    except Exception as exc:
        logger.warning("ACL SAVE 失败: {}", exc)
    logger.info("Redis ACL 全量同步: synced={} failed={}", synced, len(failed))
    return {"synced": synced, "failed": failed}


def decrypt_worker_redis_password(worker) -> str | None:
    """读出 Worker 当前 Redis 密码明文（仅 Master/Web API 内部调用）。"""
    if not worker.redis_password_encrypted:
        return None
    return secret_box.decrypt(worker.redis_password_encrypted)


__all__ = [
    "PER_WORKER_KEY_PATTERNS",
    "SHARED_KEY_PATTERNS",
    "ensure_worker_acl",
    "revoke_worker_acl",
    "sync_all_worker_acls",
    "decrypt_worker_redis_password",
]
