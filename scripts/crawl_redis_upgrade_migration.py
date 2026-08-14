"""Idempotent offline migration of paused Crawl Hash/Set state."""

from __future__ import annotations

from typing import Any

from scripts.crawl_redis_upgrade_contract import StateKeyStats

SCAN_COUNT = 500


async def migrate_state_keys(client: Any, state_keys: tuple[StateKeyStats, ...]) -> tuple[str, ...]:
    migrated: list[str] = []
    for item in state_keys:
        if item.redis_type == "hash":
            await _migrate_hash(client, item)
        elif item.redis_type == "set":
            await _migrate_set(client, item)
        else:
            raise TypeError(f"不支持迁移 Redis type={item.redis_type}: {item.source}")
        migrated.append(item.source)
    return tuple(migrated)


async def _migrate_hash(client: Any, item: StateKeyStats) -> None:
    source_type = await _redis_type(client, item.source)
    if source_type == "none":
        return
    if source_type != "hash":
        raise TypeError(f"Hash 源类型在迁移期间发生变化: {item.source}")
    target_type = await _redis_type(client, item.target)
    if target_type == "hash":
        await _finish_equal_hash(client, item)
        return
    if target_type != "none":
        raise TypeError(f"Hash 目标类型在迁移期间发生变化: {item.target}")
    payload = await client.dump(item.source)
    if not isinstance(payload, bytes):
        raise RuntimeError(f"无法读取 Hash DUMP: {item.source}")
    await client.restore(item.target, 0, payload, replace=False)
    await _finish_equal_hash(client, item)


async def _finish_equal_hash(client: Any, item: StateKeyStats) -> None:
    source = await client.hgetall(item.source)
    target = await client.hgetall(item.target)
    if source != target:
        raise RuntimeError(f"Hash 目标内容与源不一致: {item.target}")
    await client.persist(item.target)
    deleted = int(await client.delete(item.source))
    if deleted != 1:
        raise RuntimeError(f"Hash 源删除失败: {item.source}")


async def _migrate_set(client: Any, item: StateKeyStats) -> None:
    source_type = await _redis_type(client, item.source)
    if source_type == "none":
        return
    if source_type != "set":
        raise TypeError(f"Set 源类型在迁移期间发生变化: {item.source}")
    target_type = await _redis_type(client, item.target)
    if target_type not in {"none", "set"}:
        raise TypeError(f"Set 目标类型在迁移期间发生变化: {item.target}")
    batch: list[Any] = []
    async for member in client.sscan_iter(item.source, count=SCAN_COUNT):
        batch.append(member)
        if len(batch) == SCAN_COUNT:
            await client.sadd(item.target, *batch)
            batch.clear()
    if batch:
        await client.sadd(item.target, *batch)
    await _verify_set_subset(client, item)
    await client.persist(item.target)
    deleted = int(await client.delete(item.source))
    if deleted != 1:
        raise RuntimeError(f"Set 源删除失败: {item.source}")


async def _verify_set_subset(client: Any, item: StateKeyStats) -> None:
    async for member in client.sscan_iter(item.source, count=SCAN_COUNT):
        if not await client.sismember(item.target, member):
            raise RuntimeError(f"Set 目标缺少已迁移 member: {item.target}")


async def _redis_type(client: Any, key: str) -> str:
    value = await client.type(key)
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


__all__ = ["migrate_state_keys"]
