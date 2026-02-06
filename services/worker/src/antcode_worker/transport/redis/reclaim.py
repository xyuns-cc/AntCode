"""
Redis Pending 任务回收模块

实现 Worker 崩溃/重启后的 pending 任务回收逻辑。
使用 Redis Streams 的 XAUTOCLAIM 命令实现 at-least-once 语义。

Requirements: 5.3
"""

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from antcode_worker.transport.redis.keys import RedisKeys


@dataclass
class ReclaimConfig:
    """回收配置"""

    # 任务被认为是 pending 的最小时间（毫秒）
    min_idle_time_ms: int = 60000  # 1 分钟

    # 每次回收的最大任务数
    max_reclaim_count: int = 10

    # 回收检查间隔（秒）
    check_interval_seconds: float = 30.0

    # 最大重试次数（超过后移入死信队列）
    max_retries: int = 3

    # 是否启用死信队列
    enable_dead_letter: bool = True

    # 死信队列保留时间（秒）
    dead_letter_ttl_seconds: int = 86400 * 7  # 7 天


@dataclass
class ReclaimedTask:
    """回收的任务"""

    message_id: str  # Redis Stream 消息 ID
    data: dict[str, str]  # 消息数据
    idle_time_ms: int  # 空闲时间（毫秒）
    delivery_count: int  # 投递次数
    last_delivery_time: datetime | None = None

    @property
    def is_expired(self) -> bool:
        """是否已过期（投递次数过多）"""
        return self.delivery_count > 3  # 默认最大重试 3 次


@dataclass
class ReclaimStats:
    """回收统计"""

    total_reclaimed: int = 0
    total_dead_lettered: int = 0
    last_reclaim_time: datetime | None = None
    reclaim_errors: int = 0

    # 按 stream 统计
    stream_stats: dict[str, int] = field(default_factory=dict)


class PendingTaskReclaimer:
    """
    Pending 任务回收器

    负责回收因 Worker 崩溃/断线而未完成的任务。
    使用 XAUTOCLAIM 命令自动获取超时的 pending 消息。

    Requirements: 5.3
    """

    def __init__(
        self,
        redis_client: Any,
        worker_id: str,
        keys: RedisKeys | None = None,
        config: ReclaimConfig | None = None,
    ):
        """
        初始化回收器

        Args:
            redis_client: Redis 异步客户端
            worker_id: 当前 Worker ID
            keys: Redis key 生成器
            config: 回收配置
        """
        self._redis = redis_client
        self._worker_id = worker_id
        self._keys = keys or RedisKeys()
        self._config = config or ReclaimConfig()
        self._stats = ReclaimStats()
        self._running = False
        self._reclaim_task: asyncio.Task | None = None

    @property
    def stats(self) -> ReclaimStats:
        """获取统计信息"""
        return self._stats

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running

    async def start(self) -> None:
        """启动回收器"""
        if self._running:
            return

        self._running = True
        self._reclaim_task = asyncio.create_task(self._reclaim_loop())

    async def stop(self) -> None:
        """停止回收器"""
        self._running = False

        if self._reclaim_task:
            self._reclaim_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reclaim_task
            self._reclaim_task = None

    async def _reclaim_loop(self) -> None:
        """回收循环"""
        while self._running:
            try:
                await self._do_reclaim()
            except asyncio.CancelledError:
                break
            except Exception:
                self._stats.reclaim_errors += 1

            await asyncio.sleep(self._config.check_interval_seconds)

    async def _do_reclaim(self) -> list[ReclaimedTask]:
        """
        执行一次回收

        Returns:
            回收的任务列表
        """
        reclaimed_tasks: list[ReclaimedTask] = []

        # 获取任务 ready stream
        stream_key = self._keys.task_ready_stream(self._worker_id)
        group_name = self._keys.consumer_group_name()
        consumer_name = self._keys.consumer_name(self._worker_id)

        try:
            # 使用 XAUTOCLAIM 回收 pending 任务
            # XAUTOCLAIM key group consumer min-idle-time start [COUNT count]
            result = await self._redis.xautoclaim(
                stream_key,
                group_name,
                consumer_name,
                min_idle_time=self._config.min_idle_time_ms,
                start_id="0-0",
                count=self._config.max_reclaim_count,
            )

            if not result:
                return reclaimed_tasks

            # 解析结果
            # result = [next_start_id, [[msg_id, {fields}], ...], [deleted_ids]]
            next_start_id, messages, deleted_ids = result

            for msg_id, msg_data in messages:
                # 获取消息的 pending 信息
                pending_info = await self._get_pending_info(
                    stream_key, group_name, msg_id
                )

                task = ReclaimedTask(
                    message_id=msg_id,
                    data=msg_data,
                    idle_time_ms=pending_info.get("idle_time_ms", 0),
                    delivery_count=pending_info.get("delivery_count", 1),
                    last_delivery_time=pending_info.get("last_delivery_time"),
                )

                # 检查是否超过最大重试次数
                if task.delivery_count > self._config.max_retries:
                    if self._config.enable_dead_letter:
                        await self._move_to_dead_letter(stream_key, task)
                        self._stats.total_dead_lettered += 1
                    else:
                        # 直接 ACK 丢弃
                        await self._redis.xack(stream_key, group_name, msg_id)
                else:
                    reclaimed_tasks.append(task)
                    self._stats.total_reclaimed += 1

            # 更新统计
            self._stats.last_reclaim_time = datetime.now()
            self._stats.stream_stats[stream_key] = (
                self._stats.stream_stats.get(stream_key, 0) + len(reclaimed_tasks)
            )

        except Exception:
            self._stats.reclaim_errors += 1
            raise

        return reclaimed_tasks

    async def _get_pending_info(
        self, stream_key: str, group_name: str, message_id: str
    ) -> dict[str, Any]:
        """
        获取消息的 pending 信息

        Args:
            stream_key: Stream key
            group_name: 消费者组名
            message_id: 消息 ID

        Returns:
            pending 信息字典
        """
        try:
            # XPENDING key group [IDLE min-idle-time] start end count [consumer]
            result = await self._redis.xpending_range(
                stream_key,
                group_name,
                min=message_id,
                max=message_id,
                count=1,
            )

            if result:
                # result = [[message_id, consumer, idle_time, delivery_count], ...]
                entry = result[0]
                return {
                    "message_id": entry[0],
                    "consumer": entry[1],
                    "idle_time_ms": entry[2],
                    "delivery_count": entry[3],
                    "last_delivery_time": datetime.now()
                    - timedelta(milliseconds=entry[2]),
                }
        except Exception:
            pass

        return {"idle_time_ms": 0, "delivery_count": 1}

    async def _move_to_dead_letter(
        self, source_stream: str, task: ReclaimedTask
    ) -> None:
        """
        将任务移入死信队列

        Args:
            source_stream: 源 Stream key
            task: 要移入的任务
        """
        # 死信队列 key
        dead_letter_key = f"{source_stream}:dead_letter"

        # 添加元数据
        dead_letter_data = dict(task.data)
        dead_letter_data["_original_stream"] = source_stream
        dead_letter_data["_original_message_id"] = task.message_id
        dead_letter_data["_delivery_count"] = str(task.delivery_count)
        dead_letter_data["_dead_lettered_at"] = datetime.now().isoformat()
        dead_letter_data["_idle_time_ms"] = str(task.idle_time_ms)

        # 写入死信队列
        await self._redis.xadd(
            dead_letter_key,
            dead_letter_data,
            maxlen=10000,  # 限制死信队列大小
        )

        # 设置过期时间
        await self._redis.expire(dead_letter_key, self._config.dead_letter_ttl_seconds)

        # ACK 原消息
        group_name = self._keys.consumer_group_name()
        await self._redis.xack(source_stream, group_name, task.message_id)

    async def reclaim_once(self) -> list[ReclaimedTask]:
        """
        手动执行一次回收

        Returns:
            回收的任务列表
        """
        return await self._do_reclaim()

    async def get_pending_count(self, stream_key: str | None = None) -> int:
        """
        获取 pending 任务数量

        Args:
            stream_key: Stream key，为 None 时使用默认 key

        Returns:
            pending 任务数量
        """
        if stream_key is None:
            stream_key = self._keys.task_ready_stream(self._worker_id)

        group_name = self._keys.consumer_group_name()

        try:
            # XPENDING key group
            result = await self._redis.xpending(stream_key, group_name)
            if result:
                # result = [pending_count, min_id, max_id, [[consumer, count], ...]]
                return result[0]
        except Exception:
            pass

        return 0

    async def get_pending_summary(
        self, stream_key: str | None = None
    ) -> dict[str, Any]:
        """
        获取 pending 任务摘要

        Args:
            stream_key: Stream key

        Returns:
            摘要信息
        """
        if stream_key is None:
            stream_key = self._keys.task_ready_stream(self._worker_id)

        group_name = self._keys.consumer_group_name()

        try:
            result = await self._redis.xpending(stream_key, group_name)
            if result:
                pending_count, min_id, max_id, consumers = result
                return {
                    "pending_count": pending_count,
                    "min_id": min_id,
                    "max_id": max_id,
                    "consumers": {c[0]: c[1] for c in (consumers or [])},
                }
        except Exception:
            pass

        return {"pending_count": 0, "min_id": None, "max_id": None, "consumers": {}}


class GlobalReclaimer:
    """
    全局回收器

    用于回收所有 Worker 的 pending 任务（通常由平台作业或专门的回收服务运行）。
    """

    def __init__(
        self,
        redis_client: Any,
        keys: RedisKeys | None = None,
        config: ReclaimConfig | None = None,
    ):
        """
        初始化全局回收器

        Args:
            redis_client: Redis 异步客户端
            keys: Redis key 生成器
            config: 回收配置
        """
        self._redis = redis_client
        self._keys = keys or RedisKeys()
        self._config = config or ReclaimConfig()
        self._stats = ReclaimStats()

    async def scan_and_reclaim(self) -> dict[str, list[ReclaimedTask]]:
        """
        扫描并回收所有 Worker 的 pending 任务

        Returns:
            按 Worker ID 分组的回收任务
        """
        result: dict[str, list[ReclaimedTask]] = {}

        # 获取所有 Worker
        worker_set_key = self._keys.worker_set()
        worker_ids = await self._redis.smembers(worker_set_key)

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
        """
        获取全局 pending 任务摘要

        Returns:
            按 Worker ID 分组的摘要信息
        """
        result: dict[str, dict[str, Any]] = {}

        worker_set_key = self._keys.worker_set()
        worker_ids = await self._redis.smembers(worker_set_key)

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
    start_id: str = "0",
) -> bool:
    """
    确保消费者组存在

    Args:
        redis_client: Redis 客户端
        stream_key: Stream key
        group_name: 消费者组名
        start_id: 起始 ID

    Returns:
        是否成功创建或已存在
    """
    try:
        # 尝试创建消费者组
        await redis_client.xgroup_create(
            stream_key,
            group_name,
            id=start_id,
            mkstream=True,  # 如果 stream 不存在则创建
        )
        return True
    except Exception as e:
        # 如果组已存在，忽略错误
        if "BUSYGROUP" in str(e):
            return True
        raise


async def cleanup_dead_consumers(
    redis_client: Any,
    stream_key: str,
    group_name: str,
    max_idle_time_ms: int = 300000,  # 5 分钟
) -> list[str]:
    """
    清理死亡的消费者

    Args:
        redis_client: Redis 客户端
        stream_key: Stream key
        group_name: 消费者组名
        max_idle_time_ms: 最大空闲时间（毫秒）

    Returns:
        被清理的消费者列表
    """
    cleaned: list[str] = []

    try:
        # 获取消费者信息
        consumers = await redis_client.xinfo_consumers(stream_key, group_name)

        for consumer in consumers:
            name = consumer.get("name")
            idle = consumer.get("idle", 0)
            pending = consumer.get("pending", 0)

            # 如果消费者空闲时间过长且没有 pending 消息，删除它
            if idle > max_idle_time_ms and pending == 0:
                await redis_client.xgroup_delconsumer(stream_key, group_name, name)
                cleaned.append(name)

    except Exception:
        pass

    return cleaned
