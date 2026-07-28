"""Administrative helpers for Redis task consumer groups."""

from typing import Any

from antcode_worker.transport.redis.keys import RedisKeys
from antcode_worker.transport.redis.reclaim_models import ReclaimConfig, ReclaimedTask, ReclaimStats


class GlobalReclaimer:
    """Scan pending task streams for all registered workers."""

    def __init__(
        self,
        redis_client: Any,
        keys: RedisKeys | None = None,
        config: ReclaimConfig | None = None,
    ) -> None:
        self._redis = redis_client
        self._keys = keys or RedisKeys()
        self._config = config or ReclaimConfig()
        self._stats = ReclaimStats()

    async def scan_and_reclaim(self) -> dict[str, list[ReclaimedTask]]:
        from antcode_worker.transport.redis.reclaim import PendingTaskReclaimer

        result: dict[str, list[ReclaimedTask]] = {}
        worker_ids = await self._redis.smembers(self._keys.worker_set())
        for worker_id in worker_ids:
            reclaimer = PendingTaskReclaimer(
                redis_client=self._redis,
                worker_id=worker_id,
                keys=self._keys,
                config=self._config,
            )
            tasks = await reclaimer.reclaim_once()
            if tasks:
                result[worker_id] = tasks
        return result

    async def get_global_pending_summary(self) -> dict[str, dict[str, Any]]:
        from antcode_worker.transport.redis.reclaim import PendingTaskReclaimer

        result: dict[str, dict[str, Any]] = {}
        worker_ids = await self._redis.smembers(self._keys.worker_set())
        for worker_id in worker_ids:
            reclaimer = PendingTaskReclaimer(
                redis_client=self._redis,
                worker_id=worker_id,
                keys=self._keys,
                config=self._config,
            )
            summary = await reclaimer.get_pending_summary()
            if summary["pending_count"] > 0:
                result[worker_id] = summary
        return result


async def ensure_consumer_group(
    redis_client: Any,
    stream_key: str,
    group_name: str,
    *,
    start_id: str = "0",
) -> bool:
    try:
        await redis_client.xgroup_create(
            stream_key,
            group_name,
            id=start_id,
            mkstream=True,
        )
        return True
    except Exception as exc:
        if "BUSYGROUP" in str(exc):
            return True
        raise


async def cleanup_dead_consumers(
    redis_client: Any,
    stream_key: str,
    group_name: str,
    *,
    max_idle_time_ms: int = 300_000,
) -> list[str]:
    cleaned: list[str] = []
    consumers = await redis_client.xinfo_consumers(stream_key, group_name)
    for consumer in consumers:
        name = consumer.get("name")
        idle = consumer.get("idle", 0)
        pending = consumer.get("pending", 0)
        if idle > max_idle_time_ms and pending == 0:
            await redis_client.xgroup_delconsumer(stream_key, group_name, name)
            cleaned.append(name)
    return cleaned
