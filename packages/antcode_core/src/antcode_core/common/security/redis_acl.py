"""Direct Worker 独立 Redis ACL 凭证管理。

给每个 Direct Worker 发一个 ``worker_<public_id>`` 账户，
通过 ACL 规则限制其只能访问：
- 自己的 per-worker key（task:ready:<id>、heartbeat:<id> 等）
- 必要的共享 stream（task:result、log:ingest、控制全局流等）

防止单 worker 被攻破后横向读写其他 worker 的数据。
selector 策略表见同目录 ``redis_acl_policy.py``。

残余风险（P1-SEC-02 复核结论；Redis ACL 表达力边界，经 Redis 8 实测
确认，如实记录、不视为已解决）：

1. ``{ns}:run:owner:*`` 与 ``{ns}:lease:expiring`` / ``{ns}:lease:active``：
   Redis 7+ 起 Lua 脚本内部的每次 ``redis.call`` 同样按调用用户 ACL 逐
   命令、逐 key 校验（实测：仅授 ``+eval`` 时脚本内 SET 直接被拒）。
   因此 run_ownership_fence 的 SET/GET/DEL/PEXPIRE 与 LeaseStore
   grant/revoke Lua 的 ZADD/ZREM、SADD/SREM 必须以裸命令授予；ACL 无法
   区分"脚本内调用"与"客户端裸调用"，也没有 zset/set 成员维度。被攻破
   的 worker 因此可以绕过 fence Lua 直接改写任意 run 的 ownership key、
   污染共享 lease 索引。另外 P1-DR-02 死主接管要求 takeover Lua 只读
   （HGET/PTTL）任意 holder 的 ``lease:data:*``：被攻破的 worker 由此可
   读取他人 lease 元数据（含 lease_id），并结合裸 SET 伪造形如
   ``victim:victim_lease`` 的 ownership token；lease Hash 的写命令仍只
   限自己的 ``lease:data:<wid>``。应用层缓解：结算/派发侧始终以权威
   Lease Hash（按 worker 隔离、mTLS/凭证绑定身份）复核代际，ownership
   key 与共享索引都不是授权的最终依据。
2. ``{ns}:spider:{{*}}:data|meta|tombstone`` 与 ``{ns}:spider:index:*``：
   spider key 以 run_id/project_id 为维度，key 布局中没有 worker 维度，
   ACL 按 key pattern 无法表达"只许写自己 own 的 run"。被攻破的 worker
   可写任意 run 的 spider 数据/元数据、增删任意 project 的 index 成员。
3. ``{ns}:control:global``：共享广播 stream 上按 worker 命名 consumer
   group（``{ns}-control:<wid>``），ACL 不覆盖 group/consumer 维度；被
   攻破的 worker 可对他人 group 执行 XREADGROUP/XACK（吞掉他人广播）。
   worker 自身 ack 走 owned_stream_ack Lua（XPENDING owner 校验）仅防
   误 ack，不能阻止恶意 ack。

以上 1-3 需要 key 布局或协议改造（如 ownership 结算全部收拢到
master/gateway 凭证、spider key 引入 worker 维度）才能根除，超出 ACL
调整范围。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

# P1-DR-02 (round6): 模块顶部 import 便于测试 monkeypatch 绕开 tortoise 初始化
from tortoise.transactions import in_transaction

from antcode_core.common.config import settings
from antcode_core.common.security.api_key import generate_api_key

# 策略表拆到 redis_acl_policy.py（复杂度门禁 FILE_LINES 约束）；此处
# re-export 维持既有导入路径不变。
from antcode_core.common.security.redis_acl_policy import (
    ACL_BASE_COMMAND_RULES,
    ACL_SELECTOR_SPECS,
)
from antcode_core.common.security.redis_acl_recovery import (
    restore_acl_fields,
    rollback_or_raise,
    snapshot_acl,
)
from antcode_core.common.security.secret_box import secret_box
from antcode_core.infrastructure.redis.runtime_control_keys import validate_worker_key_segment


def _acl_username(worker_public_id: str) -> str:
    return f"worker_{worker_public_id}"


def _build_setuser_args(
    username: str,
    password: str,
    worker_public_id: str,
    *,
    namespace: str,
) -> list[str]:
    """组装 ACL SETUSER 命令的参数列表。"""
    worker_public_id = validate_worker_key_segment(worker_public_id)
    args: list[str] = [
        "SETUSER",
        username,
        "reset",
        "on",
        f">{password}",
    ]
    for commands, access, patterns in ACL_SELECTOR_SPECS:
        keys = (f"{access}~{pattern.format(ns=namespace, wid=worker_public_id)}" for pattern in patterns)
        args.append(f"({' '.join((*commands, *keys))})")
    args.extend(ACL_BASE_COMMAND_RULES)
    return args


async def ensure_worker_acl(
    redis_admin_client,
    worker,
    *,
    new_password: str | None = None,
) -> str:
    """建立或刷新 Worker 的 Redis ACL，返回仅展示一次的明文密码。

    P1-DR-02 (round6): 并发轮换加 SELECT FOR UPDATE 行锁。之前两个并发
    ensure_worker_acl:
    - 都读 revision=N
    - 都 Redis SETUSER (新密码 A/B, 后写覆盖 = B)
    - 都 PG save revision=N+1 (password_encrypted = 各自的, PG 后写 = A)
    - 最终 Redis=B / PG=A 分裂,Worker 用 PG 里的 A 认证 Redis 会失败
    - Worker 永久失联,除非人工重新轮换

    行锁让两个调用串行:第 1 个拿锁 → Redis SETUSER + PG save → 释放;
    第 2 个进入,拿锁,从 PG 重读 revision=N+1,继续 SETUSER N+2,保持
    Redis 与 PG 一致。
    """
    if not settings.REDIS_ACL_ENABLED:
        raise RuntimeError("REDIS_ACL_ENABLED=false 时不应调用 ensure_worker_acl")

    # P1-DR-02 (round6): 事务内 SELECT FOR UPDATE + 重读 worker 保证串行
    # 单测用 SimpleNamespace 绕过 tortoise, 检测 worker.id 缺失时降级(仅测试路径,
    # 生产 Worker ORM 一定有 id 字段)
    has_orm_id = getattr(worker, "id", None) is not None
    if has_orm_id:
        from antcode_core.domain.models.worker import Worker

        async with in_transaction("default") as conn:
            fresh = await Worker.filter(id=worker.id).using_db(conn).select_for_update().first()
            if fresh is None:
                raise RuntimeError(f"Worker {worker.public_id} 已被删除,无法签发 ACL")
            # 用 fresh 覆盖 caller 传入的 worker 相关字段,避免 stale revision
            worker.redis_username = fresh.redis_username
            worker.redis_password_encrypted = fresh.redis_password_encrypted
            worker.redis_acl_revision = fresh.redis_acl_revision
            worker.redis_acl_synced_at = fresh.redis_acl_synced_at
            return await _ensure_worker_acl_within_tx(redis_admin_client, worker, new_password=new_password, conn=conn)

    # 测试路径: 无 ORM id 时跳过锁, 直接执行原逻辑
    return await _ensure_worker_acl_within_tx(redis_admin_client, worker, new_password=new_password, conn=None)


async def _ensure_worker_acl_within_tx(
    redis_admin_client,
    worker,
    *,
    new_password: str | None = None,
    conn: Any = None,
) -> str:
    """P1-DR-02 (round6): ensure_worker_acl 的核心逻辑,已在锁内。"""
    plaintext_password = new_password or generate_api_key(prefix="rk", length=32)
    username = _acl_username(worker.public_id)
    snapshot = snapshot_acl(worker)

    args = _build_setuser_args(
        username,
        plaintext_password,
        worker.public_id,
        namespace=settings.REDIS_NAMESPACE,
    )
    redis_mutation_attempted = False
    try:
        redis_mutation_attempted = True
        await redis_admin_client.execute_command("ACL", *args)
        await redis_admin_client.execute_command("ACL", "SAVE")
        worker.redis_username = username
        worker.redis_password_encrypted = secret_box.encrypt(plaintext_password)
        worker.redis_acl_revision = snapshot.revision + 1
        worker.redis_acl_synced_at = datetime.now(UTC)
        await _save_acl_fields(worker, conn=conn)
    except BaseException as exc:
        restore_acl_fields(worker, snapshot)
        if redis_mutation_attempted:
            await rollback_or_raise(
                redis_admin_client,
                worker,
                snapshot=snapshot,
                current_username=username,
                original_error=exc,
                build_setuser_args=_build_setuser_args,
                namespace=settings.REDIS_NAMESPACE,
            )
        raise
    logger.info(
        "Worker {} Redis ACL 已签发: user={} revision={}",
        worker.public_id,
        username,
        worker.redis_acl_revision,
    )
    return plaintext_password


async def revoke_worker_acl(
    redis_admin_client,
    worker,
    *,
    clear_credentials: bool = True,
) -> None:
    """删除 Worker 的 Redis ACL（worker 注销时调用）。"""
    if not worker.redis_username:
        return
    snapshot = snapshot_acl(worker)
    redis_mutation_attempted = False
    try:
        redis_mutation_attempted = True
        await redis_admin_client.execute_command("ACL", "DELUSER", snapshot.username)
        await redis_admin_client.execute_command("ACL", "SAVE")
        if not clear_credentials:
            return
        worker.redis_username = None
        worker.redis_password_encrypted = None
        worker.redis_acl_synced_at = None
        await worker.save(update_fields=["redis_username", "redis_password_encrypted", "redis_acl_synced_at"])
    except BaseException as exc:
        restore_acl_fields(worker, snapshot)
        if redis_mutation_attempted:
            await rollback_or_raise(
                redis_admin_client,
                worker,
                snapshot=snapshot,
                current_username=snapshot.username,
                original_error=exc,
                build_setuser_args=_build_setuser_args,
                namespace=settings.REDIS_NAMESPACE,
            )
        raise


async def _save_acl_fields(worker: Any, *, conn: Any = None) -> None:
    """P1-DR-02 (round6): conn 可选传入,让 save 走同一事务(与 SELECT FOR UPDATE)."""
    save_kwargs = {
        "update_fields": [
            "redis_username",
            "redis_password_encrypted",
            "redis_acl_revision",
            "redis_acl_synced_at",
        ]
    }
    if conn is not None:
        save_kwargs["using_db"] = conn
    await worker.save(**save_kwargs)


async def sync_all_worker_acls(redis_admin_client) -> dict[str, Any]:
    """Master 启动时把 PG 中已有 ACL 凭证的 worker 全量推到 Redis。

    用于 Redis 重启 / 跨环境迁移时恢复。worker 表才是单一权威。
    """
    from antcode_core.domain.models.worker import Worker

    if not settings.REDIS_ACL_ENABLED:
        return {"skipped": True, "reason": "REDIS_ACL_ENABLED=false"}

    synced = 0
    revoked = 0
    failed: list[str] = []
    async for worker in Worker.filter(redis_username__not_isnull=True):
        try:
            outcome = await _sync_worker_acl(redis_admin_client, worker)
            synced += int(outcome == "synced")
            revoked += int(outcome == "revoked")
        except Exception as exc:
            logger.warning("同步 ACL 失败 worker={} err={}", worker.public_id, exc)
            failed.append(worker.public_id)
    await redis_admin_client.execute_command("ACL", "SAVE")
    if failed:
        raise RuntimeError(f"Redis ACL 全量同步失败: {failed}")
    logger.info("Redis ACL 全量同步: synced={} revoked={} failed={}", synced, revoked, len(failed))
    return {"synced": synced, "revoked": revoked, "failed": failed}


async def _sync_worker_acl(redis_admin_client, worker) -> str:
    if worker.redis_acl_synced_at is None:
        await revoke_worker_acl(redis_admin_client, worker, clear_credentials=True)
        return "revoked"
    plaintext = secret_box.decrypt(worker.redis_password_encrypted)
    args = _build_setuser_args(
        worker.redis_username,
        plaintext,
        worker.public_id,
        namespace=settings.REDIS_NAMESPACE,
    )
    await redis_admin_client.execute_command("ACL", *args)
    return "synced"


def decrypt_worker_redis_password(worker) -> str | None:
    """读出 Worker 当前 Redis 密码明文（仅 Master/Web API 内部调用）。"""
    if not worker.redis_password_encrypted:
        return None
    return secret_box.decrypt(worker.redis_password_encrypted)


__all__ = [
    "ACL_BASE_COMMAND_RULES",
    "ACL_SELECTOR_SPECS",
    "ensure_worker_acl",
    "revoke_worker_acl",
    "sync_all_worker_acls",
    "decrypt_worker_redis_password",
]
