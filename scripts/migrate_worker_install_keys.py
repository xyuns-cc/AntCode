"""迁移旧版 PostgreSQL 与 Redis 中的明文 Worker 安装 Key。

必须在所有 AntCode 进程停止后执行。Redis Cluster 中源键和目标键可能位于
不同 slot，因此迁移使用 TYPE/DUMP/PTTL/RESTORE/DEL，而不使用 RENAME。
Redis 搬迁全部完成后才提交 PostgreSQL pending 行；中断后可重跑收敛。
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "antcode_core" / "src"))

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_REDIS_KEY_PATTERN = re.compile(
    r"^(?P<prefix>.+:worker:install-key:(?P<action>fail|block|claim|nonce|meta):)"
    r"(?P<token>[0-9A-F]{32})(?::(?P<secondary>.*))?$"
)
_REDIS_ACTIONS_WITH_SECONDARY = frozenset({"fail", "block", "nonce"})
_SCAN_COUNT = 500
_MILLISECONDS_PER_SECOND = 1000
_PERSISTENT_PTTL_MS = -1
_MISSING_PTTL_MS = -2
_RESTORE_PERSISTENT_TTL_MS = 0


class RedisKeyMigrationConflict(RuntimeError):
    """旧键与已存在的摘要键内容不同，无法安全自动合并。"""


def _monotonic_ms() -> int:
    return int(time.monotonic() * _MILLISECONDS_PER_SECOND)


@dataclass(frozen=True)
class _RedisKeySnapshot:
    payload: bytes
    pttl_ms: int
    captured_at_ms: int

    def remaining_pttl_ms(self) -> int:
        if self.pttl_ms == _PERSISTENT_PTTL_MS:
            return _PERSISTENT_PTTL_MS
        elapsed_ms = max(0, _monotonic_ms() - self.captured_at_ms)
        return self.pttl_ms - elapsed_ms


def _is_hashed_install_key(value: str) -> bool:
    return bool(_SHA256_HEX_PATTERN.fullmatch(value or ""))


def _metadata_keys(namespace: str, stored_key: str) -> tuple[str, ...]:
    from antcode_core.domain.models.worker_install_key import WorkerInstallKey

    digest = stored_key if _is_hashed_install_key(stored_key) else WorkerInstallKey.hash_plaintext(stored_key)
    hashed_key = f"{namespace}:worker:install-key:meta:{digest}"
    if _is_hashed_install_key(stored_key):
        return (hashed_key,)
    legacy_key = f"{namespace}:worker:install-key:meta:{stored_key}"
    return (legacy_key, hashed_key)


def _parse_allowed_source(raw: str | bytes, *, row_id: int) -> str | None:
    from antcode_core.common.security.network_source import normalize_ip_or_cidr

    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise TypeError("metadata is not an object")
        return normalize_ip_or_cidr(payload.get("allowed_source"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeError(f"pending 安装 Key 来源元数据无效，拒绝迁移: id={row_id}") from exc


async def _recover_allowed_source(
    redis_client,
    namespace: str,
    stored_key: str,
    *,
    row_id: int,
) -> str | None:
    recovered: set[str | None] = set()
    for metadata_key in _metadata_keys(namespace, stored_key):
        raw = await redis_client.get(metadata_key)
        if raw is not None:
            recovered.add(_parse_allowed_source(raw, row_id=row_id))
    if not recovered:
        raise RuntimeError(
            f"pending 安装 Key 缺少 Redis 来源元数据，无法判断历史来源限制: id={row_id}；请撤销并重建该 Key"
        )
    if len(recovered) != 1:
        raise RuntimeError(f"pending 安装 Key 的 Redis 来源元数据冲突，拒绝迁移: id={row_id}")
    return recovered.pop()


async def _migrate_rows(connection, redis_client, namespace: str) -> int:
    from antcode_core.common.security.network_source import normalize_ip_or_cidr
    from antcode_core.domain.models.worker_install_key import WorkerInstallKey

    expired, _ = await connection.execute_query(
        'UPDATE "worker_install_keys" SET "status" = $1 WHERE "status" = $2 AND "expires_at" <= CURRENT_TIMESTAMP',
        ["expired", "pending"],
    )
    rows = await connection.execute_query_dict(
        'SELECT "id", "key", "allowed_source" FROM "worker_install_keys" WHERE "status" = $1',
        ["pending"],
    )
    migrated = int(expired)
    for row in rows:
        stored_key = str(row.get("key") or "")
        if not stored_key:
            raise RuntimeError(f"pending 安装 Key 为空，拒绝迁移: id={row['id']}")
        current_source = normalize_ip_or_cidr(row.get("allowed_source"))
        recovered_source = current_source
        if current_source is None:
            recovered_source = await _recover_allowed_source(
                redis_client,
                namespace,
                stored_key,
                row_id=row["id"],
            )
        hashed = stored_key if _is_hashed_install_key(stored_key) else WorkerInstallKey.hash_plaintext(stored_key)
        if hashed == stored_key and recovered_source == current_source:
            continue
        updated, _ = await connection.execute_query(
            'UPDATE "worker_install_keys" SET "key" = $1, "allowed_source" = $2 '
            'WHERE "id" = $3 AND "key" = $4 AND "status" = $5',
            [hashed, recovered_source, row["id"], stored_key, "pending"],
        )
        if updated != 1:
            raise RuntimeError(f"安装 Key 迁移并发冲突: id={row['id']}")
        migrated += 1
    return migrated


def _migrated_redis_key(raw_key: str | bytes) -> str | None:
    from antcode_core.infrastructure.redis.control_plane import install_key_redis_digest

    text = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
    match = _LEGACY_REDIS_KEY_PATTERN.fullmatch(text)
    if match is None:
        return None
    action = match.group("action")
    secondary = match.group("secondary")
    if (action in _REDIS_ACTIONS_WITH_SECONDARY) != (secondary is not None):
        raise RuntimeError(f"旧 install-key Redis key 格式无效: {text}")
    suffix = install_key_redis_digest(match.group("token"))
    if secondary is not None:
        suffix = f"{suffix}:{install_key_redis_digest(secondary)}"
    return f"{match.group('prefix')}{suffix}"


async def _migrate_redis_keys(redis_client, namespace: str) -> int:
    migrated = 0
    pattern = f"{namespace}:worker:install-key:*"
    async for source_key in redis_client.scan_iter(match=pattern, count=_SCAN_COUNT):
        target_key = _migrated_redis_key(source_key)
        if target_key is None:
            continue
        snapshot = await _read_redis_snapshot(redis_client, source_key)
        if snapshot is None:
            continue
        if await _migrate_redis_key(redis_client, source_key, target_key, snapshot=snapshot):
            migrated += 1
    return migrated


def _decode_redis_key(raw_key: str | bytes) -> str:
    return raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key


async def _read_redis_snapshot(redis_client, raw_key: str | bytes) -> _RedisKeySnapshot | None:
    key = _decode_redis_key(raw_key)
    redis_type = _decode_redis_key(await redis_client.type(key))
    if redis_type == "none":
        return None
    if redis_type != "string":
        raise TypeError(f"install-key Redis key 必须为 string: key={key}, type={redis_type}")
    payload = await redis_client.dump(key)
    pttl_ms = int(await redis_client.pttl(key))
    if payload is None or pttl_ms == _MISSING_PTTL_MS:
        return None
    if pttl_ms == _RESTORE_PERSISTENT_TTL_MS or pttl_ms < _PERSISTENT_PTTL_MS:
        raise RuntimeError(f"install-key Redis key 的 TTL 无效: key={key}, pttl={pttl_ms}")
    if not isinstance(payload, bytes):
        raise TypeError(f"Redis DUMP 必须返回 bytes: key={key}")
    return _RedisKeySnapshot(payload, pttl_ms, _monotonic_ms())


async def _migrate_redis_key(
    redis_client,
    raw_source: str | bytes,
    target: str,
    *,
    snapshot: _RedisKeySnapshot,
) -> bool:
    source = _decode_redis_key(raw_source)
    target_snapshot = await _read_redis_snapshot(redis_client, target)
    if target_snapshot is not None:
        return await _resolve_existing_target(
            redis_client,
            source,
            target,
            source_snapshot=snapshot,
            target_snapshot=target_snapshot,
        )
    remaining = snapshot.remaining_pttl_ms()
    if remaining != _PERSISTENT_PTTL_MS and remaining <= 0:
        return False
    restore_ttl = _RESTORE_PERSISTENT_TTL_MS if remaining == _PERSISTENT_PTTL_MS else remaining
    await redis_client.restore(target, restore_ttl, snapshot.payload, replace=False)
    return await _delete_source_after_restore(redis_client, source, target)


async def _resolve_existing_target(
    redis_client,
    source: str,
    target: str,
    *,
    source_snapshot: _RedisKeySnapshot,
    target_snapshot: _RedisKeySnapshot,
) -> bool:
    if source_snapshot.payload != target_snapshot.payload:
        raise RedisKeyMigrationConflict(f"install-key Redis 迁移目标内容冲突: {source} -> {target}")
    remaining = source_snapshot.remaining_pttl_ms()
    if remaining != _PERSISTENT_PTTL_MS and remaining <= 0:
        await redis_client.delete(target)
        return False
    if remaining == _PERSISTENT_PTTL_MS:
        await redis_client.persist(target)
    else:
        await redis_client.pexpire(target, remaining)
    return await _delete_source_after_restore(redis_client, source, target)


async def _delete_source_after_restore(redis_client, source: str, target: str) -> bool:
    if int(await redis_client.delete(source)) == 1:
        return True
    if _decode_redis_key(await redis_client.type(source)) == "none":
        await redis_client.delete(target)
        return False
    raise RuntimeError(f"install-key Redis 目标已写入，但源 key 未能删除: {source}")


async def main() -> None:
    load_dotenv(ROOT / ".env")

    from antcode_core.infrastructure.db.tortoise import close_db, init_db
    from antcode_core.infrastructure.redis import (
        close_redis_pool,
        get_redis_client,
        redis_namespace,
    )
    from tortoise.transactions import in_transaction

    namespace = redis_namespace()
    redis_client = await get_redis_client()
    try:
        await init_db(service="web_api")
        try:
            redis_migrated = await _migrate_redis_keys(redis_client, namespace)
            print(f"已迁移 {redis_migrated} 个 install-key Redis key")
            async with in_transaction("default") as connection:
                migrated = await _migrate_rows(connection, redis_client, namespace)
            print(f"已迁移 {migrated} 个 pending Worker 安装 Key")
        finally:
            await close_db()
    finally:
        await close_redis_pool()


if __name__ == "__main__":
    asyncio.run(main())
