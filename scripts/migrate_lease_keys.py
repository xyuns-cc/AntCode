"""把老版 lease key 离线迁移到当前 namespace hash-tag 命名。

老版：`{ns}:lease:{worker_id}` → 当前：`{{ns}}:lease:data:{worker_id}`

Cluster 中源和目标位于不同 slot，禁止 RENAME。脚本使用
TYPE/DUMP/PTTL 读取源快照，RESTORE 目标后再 DEL 源 key。这两步不是原子
操作，必须在所有 AntCode 进程停止后执行。

崩溃恢复（P1-DR-03）：索引重建按目标 pattern SCAN 派生——任意步骤崩溃
后重跑即可收敛；源+目标并存时重跑加 ``--on-conflict replace``。

用法：
    uv run python scripts/migrate_lease_keys.py --url redis://:pwd@host:6379 --namespace antcode

默认 dry-run，加 `--apply` 才写入。目标存在时默认报错，必须显式选择
`--on-conflict skip` 或 `--on-conflict replace`。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass
from inspect import isawaitable
from typing import Literal

from antcode_core.infrastructure.redis.factory import create_async_redis_client

ConflictPolicy = Literal["error", "skip", "replace"]
SCAN_COUNT = 200
CONFLICT_POLICIES = frozenset({"error", "skip", "replace"})
MILLISECONDS_PER_SECOND = 1000


class MigrationConflictError(RuntimeError):
    """目标 key 已存在且未指定覆盖策略。"""


def _monotonic_ms() -> int:
    return int(time.monotonic() * MILLISECONDS_PER_SECOND)


@dataclass(frozen=True)
class KeySnapshot:
    redis_type: str
    payload: bytes
    pttl_ms: int
    # 快照采集时刻（monotonic 毫秒）。RESTORE 时按流逝时间扣减 TTL，
    # 避免迁移耗时把 Lease "原样 TTL" 复活/延寿（DR-04）。
    captured_at_monotonic_ms: int

    def remaining_pttl_ms(self) -> int:
        elapsed = _monotonic_ms() - self.captured_at_monotonic_ms
        return self.pttl_ms - max(0, elapsed)


@dataclass(frozen=True)
class MigrationEntry:
    source: str
    target: str
    snapshot: KeySnapshot


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


async def _iter_old_keys(client, pattern: str):
    """SCAN 老版 lease key；scan_iter 在 Cluster 中会遍历所有主分片。"""
    async for key in client.scan_iter(match=pattern, count=SCAN_COUNT):
        yield key.decode() if isinstance(key, bytes) else key


async def _read_snapshot(client, source: str) -> KeySnapshot | None:
    redis_type = _decode(await client.type(source))
    if redis_type == "none":
        return None
    if redis_type != "hash":
        raise TypeError(f"lease 源 key 类型必须为 hash: key={source}, type={redis_type}")
    payload = await client.dump(source)
    pttl_ms = int(await client.pttl(source))
    if payload is None or pttl_ms == -2:
        return None
    if pttl_ms <= 0:
        raise RuntimeError(f"lease 源 key 缺少有效 TTL: key={source}, pttl={pttl_ms}")
    if not isinstance(payload, bytes):
        raise TypeError(f"Redis DUMP 必须返回 bytes: key={source}")
    return KeySnapshot(
        redis_type=redis_type,
        payload=payload,
        pttl_ms=pttl_ms,
        captured_at_monotonic_ms=_monotonic_ms(),
    )


async def _target_exists(client, target: str, policy: ConflictPolicy) -> bool:
    if not await client.exists(target):
        return False
    if policy == "error":
        raise MigrationConflictError(f"迁移目标已存在: {target}")
    return True


async def _restore_then_delete(client, entry: MigrationEntry, *, replace: bool) -> bool:
    remaining = entry.snapshot.remaining_pttl_ms()
    if remaining <= 0:
        # 快照到写入之间已过期：不 RESTORE（避免复活），源 key 会自然过期。
        print(f"[expired-in-flight] 迁移期间已过期，跳过: {entry.source}")
        return False
    await client.restore(
        entry.target,
        remaining,
        entry.snapshot.payload,
        replace=replace,
    )
    deleted = int(await client.delete(entry.source))
    if deleted != 1:
        raise RuntimeError(f"目标已写入，但源 key 未能删除: {entry.source}")
    return True


async def _migrate_key(
    client,
    source: str,
    target: str,
    *,
    apply: bool,
    on_conflict: ConflictPolicy,
) -> bool:
    snapshot = await _read_snapshot(client, source)
    if snapshot is None:
        print(f"[expired] 源 key 在迁移前已过期: {source}")
        return False
    target_exists = await _target_exists(client, target, on_conflict)
    if target_exists and on_conflict == "skip":
        print(f"[skip-conflict] 保留源和目标: {source} -> {target}")
        return False
    action = "replace" if target_exists else ("move" if apply else "plan")
    print(f"[{action}] {source} -> {target} type={snapshot.redis_type} pttl={snapshot.pttl_ms}")
    if not apply:
        return True
    return await _restore_then_delete(
        client,
        MigrationEntry(source=source, target=target, snapshot=snapshot),
        replace=on_conflict == "replace",
    )


async def _migrate_keys(
    client,
    namespace: str,
    apply: bool,
    *,
    on_conflict: ConflictPolicy,
) -> list[str]:
    old_pattern = f"{namespace}:lease:*"
    skip = {f"{namespace}:lease:expiring", f"{namespace}:lease:active"}
    migrated_workers: list[str] = []
    async for key in _iter_old_keys(client, old_pattern):
        if key in skip:
            continue
        prefix = f"{namespace}:lease:"
        if not key.startswith(prefix) or not key.removeprefix(prefix):
            print(f"[skip] 非预期 key 格式: {key}")
            continue
        worker_id = key.removeprefix(prefix)
        target = f"{{{namespace}}}:lease:data:{worker_id}"
        moved = await _migrate_key(
            client,
            key,
            target,
            apply=apply,
            on_conflict=on_conflict,
        )
        if moved:
            migrated_workers.append(worker_id)
    return migrated_workers


async def _rebuild_indexes(client, namespace: str, worker_ids: list[str], *, apply: bool) -> None:
    """按迁移后的权威 Hash 重建新版 active/expiring 索引（DR-04）。

    新 LeaseStore 只读取 ``{{ns}}:lease:active`` / ``{{ns}}:lease:expiring``；
    旧脚本跳过索引导致迁移后的 Lease 不进 list/sweep，失租清理副作用
    永不触发。索引从迁移后的 Hash 派生（而非照搬旧索引），过期成员自然
    排除。旧索引仅在没有遗留旧版 lease 数据 key 时才删除。

    复审 P1-DR-03（崩溃可恢复）：apply 模式**不信任**本次运行的内存迁移
    列表，而是 SCAN 目标 pattern ``{{ns}}:lease:data:*`` 全量重建——上一次
    运行在"源已 DEL、索引未建"之间崩溃时，重跑仍能把已迁移的 target
    补进索引（zadd/sadd 幂等）。dry-run 仍按计划列表打印。
    """
    new_expiring = f"{{{namespace}}}:lease:expiring"
    new_active = f"{{{namespace}}}:lease:active"
    if not apply:
        for worker_id in worker_ids:
            print(f"[index-plan] {new_expiring}/{new_active} += {worker_id}")
        return
    target_prefix = f"{{{namespace}}}:lease:data:"
    async for key in _iter_old_keys(client, f"{target_prefix}*"):
        worker_id = key.removeprefix(target_prefix)
        if not worker_id:
            continue
        expires_raw = await client.hget(key, "expires_at_ms")
        if expires_raw is None:
            print(f"[index-skip] 迁移后 Hash 缺失/过期，不入索引: {worker_id}")
            continue
        score = int(_decode(expires_raw))
        print(f"[index] {new_expiring}/{new_active} += {worker_id} (expires_at_ms={score})")
        await client.zadd(new_expiring, {worker_id: score})
        await client.sadd(new_active, worker_id)
    await _drop_old_indexes_if_clean(client, namespace)


async def _drop_old_indexes_if_clean(client, namespace: str) -> None:
    old_expiring = f"{namespace}:lease:expiring"
    old_active = f"{namespace}:lease:active"
    leftovers = [
        key
        async for key in _iter_old_keys(client, f"{namespace}:lease:*")
        if key not in {old_expiring, old_active} and key.startswith(f"{namespace}:lease:")
    ]
    if leftovers:
        print(f"[index] 存在 {len(leftovers)} 个未迁移旧 key，保留旧索引: {old_expiring}, {old_active}")
        return
    print(f"[index] 删除旧索引: {old_expiring}, {old_active}")
    await client.delete(old_expiring)
    await client.delete(old_active)


async def _close_client(client, failure: BaseException | None) -> None:
    try:
        await client.aclose()
    except BaseException as close_failure:
        if failure is not None:
            raise BaseExceptionGroup("Lease key 迁移与 Redis 关闭均失败", [failure, close_failure]) from failure
        raise


async def migrate(
    url: str,
    namespace: str,
    apply: bool,
    *,
    on_conflict: ConflictPolicy = "error",
) -> int:
    if on_conflict not in CONFLICT_POLICIES:
        raise ValueError(f"未知目标冲突策略: {on_conflict!r}")
    namespace = (namespace or "antcode").strip() or "antcode"
    client = create_async_redis_client(url, decode_responses=False)
    failure: BaseException | None = None
    try:
        return await _migrate_connected(client, namespace, apply, on_conflict=on_conflict)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        await _close_client(client, failure)


async def _migrate_connected(
    client,
    namespace: str,
    apply: bool,
    *,
    on_conflict: ConflictPolicy,
) -> int:
    ping_result = client.ping()
    if not isawaitable(ping_result):
        raise TypeError("异步 Redis client.ping() 返回了非 awaitable 结果")
    await ping_result
    print(f"[migrate] connected. namespace={namespace}, dry_run={not apply}")
    migrated_workers = await _migrate_keys(client, namespace, apply, on_conflict=on_conflict)
    await _rebuild_indexes(client, namespace, migrated_workers, apply=apply)
    print(f"[migrate] 完成 {len(migrated_workers)} 个 key ({'applied' if apply else 'dry-run'})")
    return len(migrated_workers)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Redis URL")
    parser.add_argument("--namespace", default="antcode")
    parser.add_argument("--apply", action="store_true", help="不加此 flag 只 dry-run")
    parser.add_argument(
        "--on-conflict",
        choices=("error", "skip", "replace"),
        default="error",
        help="目标 key 已存在时的策略（默认报错）",
    )
    args = parser.parse_args()
    asyncio.run(migrate(args.url, args.namespace, args.apply, on_conflict=args.on_conflict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
