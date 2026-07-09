"""T6-T1c: 一次性把老版 lease key 迁移到 hash-tag 命名。

老版：  `{ns}:lease:{worker_id}`      → 新版：`{ns}:{{worker_id}}:lease:data`
（`{{`/`}}` 是 Redis 集群模式的 hash tag，让同一个 worker 的 lease 数据落同 slot。）

单机部署时用不用都行；集群部署**必须**在滚动升级前跑一遍，否则老 key
仍留在旧命名下，新版代码扫不到，看起来像 lease 全丢。

用法：
    uv run python scripts/migrate_lease_keys.py --url redis://:pwd@host:6379 --namespace antcode

默认 dry-run，加 `--apply` 才真的 rename。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from antcode_core.infrastructure.redis.factory import create_async_redis_client


async def _iter_old_keys(client, pattern: str):
    """SCAN 老版命名的 lease key。避免 KEYS 阻塞。"""
    cursor = 0
    while True:
        cursor, batch = await client.scan(cursor=cursor, match=pattern, count=200)
        for key in batch:
            yield key.decode() if isinstance(key, bytes) else key
        if cursor == 0:
            return


async def migrate(url: str, namespace: str, apply: bool) -> int:
    client = create_async_redis_client(url, decode_responses=True)
    await client.ping()
    print(f"[migrate] connected. namespace={namespace}, dry_run={not apply}")

    # 老 pattern：{ns}:lease:*  但要排除 {ns}:lease:expiring / {ns}:lease:active
    old_pattern = f"{namespace}:lease:*"
    skip = {f"{namespace}:lease:expiring", f"{namespace}:lease:active"}

    migrated = 0
    async for key in _iter_old_keys(client, old_pattern):
        if key in skip:
            continue
        # 老 key 格式：{ns}:lease:{worker_id}
        parts = key.split(":", 2)
        if len(parts) < 3:
            print(f"[skip] 非预期 key 格式: {key}")
            continue
        worker_id = parts[2]
        # 若 worker_id 已含 `{`（新格式），跳过
        if "{" in worker_id:
            continue
        new_key = f"{namespace}:{{{worker_id}}}:lease:data"
        print(f"[{'move' if apply else 'plan'}] {key} → {new_key}")
        if apply:
            # RENAMENX 只在 new_key 不存在时移动；已存在就删老的（新版更权威）
            existed = await client.exists(new_key)
            if existed:
                await client.delete(key)
            else:
                await client.rename(key, new_key)
        migrated += 1

    print(f"[migrate] 完成 {migrated} 个 key ({'applied' if apply else 'dry-run'})")
    await client.aclose()
    return migrated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Redis URL")
    parser.add_argument("--namespace", default="antcode")
    parser.add_argument("--apply", action="store_true", help="不加此 flag 只 dry-run")
    args = parser.parse_args()
    asyncio.run(migrate(args.url, args.namespace, args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
