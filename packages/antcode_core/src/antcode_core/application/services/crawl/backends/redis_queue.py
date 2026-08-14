"""Redis 队列后端实现

基于 Redis Streams 实现的分布式队列，支持：
- 多优先级队列
- 消费者组
- 任务超时回收 (XAUTOCLAIM)
- 死信队列

适用于生产环境的分布式部署。

Requirements: 1.2, 1.4, 1.5, 1.6, 1.7, 1.8
"""

from loguru import logger

from antcode_core.application.services.crawl.backends import redis_keys as crawl_keys
from antcode_core.application.services.crawl.backends.base import (
    CrawlQueueBackend,
    QueueProjectDiscovery,
    QueueTask,
)
from antcode_core.application.services.crawl.backends.redis_queue_metrics import RedisQueueMetricsMixin
from antcode_core.application.services.crawl.backends.redis_queue_recovery import (
    CrawlQueueLocation,
    RedisQueueRecoveryMixin,
)
from antcode_core.domain.models.enums import Priority
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from antcode_core.infrastructure.redis.stream_client import StreamClient

# 默认配置
DEFAULT_CONSUMER_GROUP = "crawl_workers"
DEFAULT_STREAM_MAXLEN = 100000


_project_id_from_stream_key = crawl_keys.crawl_project_id_from_stream_key
get_stream_key = crawl_keys.crawl_stream_key
get_dead_letter_key = crawl_keys.crawl_dead_letter_key


def get_all_priority_keys(project_id: str, namespace: str | None = None) -> list[str]:
    """获取所有优先级的 Stream 键名（按优先级排序）

    Args:
        project_id: 项目 ID

    Returns:
        Stream 键名列表，按优先级从高到低排序
    """
    return [
        get_stream_key(project_id, Priority.HIGH, namespace),
        get_stream_key(project_id, Priority.NORMAL, namespace),
        get_stream_key(project_id, Priority.LOW, namespace),
    ]


class RedisCrawlQueueBackend(RedisQueueRecoveryMixin, RedisQueueMetricsMixin, CrawlQueueBackend):
    """Redis 队列后端实现

    基于 Redis Streams 实现高性能分布式队列：
    - 每个优先级使用独立的 Stream
    - 使用消费者组实现任务分发
    - 使用 XAUTOCLAIM 回收超时任务
    - 死信队列存储最终失败的任务

    Requirements: 1.2, 1.4, 1.5, 1.6, 1.7, 1.8
    """

    def __init__(
        self,
        stream_client: StreamClient | None = None,
        consumer_group: str = DEFAULT_CONSUMER_GROUP,
        max_stream_len: int = DEFAULT_STREAM_MAXLEN,
        *,
        namespace: str | None = None,
    ):
        """初始化 Redis 队列后端

        Args:
            stream_client: Stream 客户端，为 None 时自动创建
            consumer_group: 消费者组名称
            max_stream_len: Stream 最大长度
        """
        self._stream_client = stream_client or StreamClient()
        self._consumer_group = consumer_group
        self._max_stream_len = max_stream_len
        self._namespace = redis_namespace(namespace)
        self._reclaim_cursors: dict[str, str] = {}

    def _stream_key(self, project_id: str, priority: int) -> str:
        return get_stream_key(project_id, priority, self._namespace)

    def _dead_letter_key(self, project_id: str) -> str:
        return get_dead_letter_key(project_id, self._namespace)

    def _dedup_key(self, project_id: str) -> str:
        return crawl_keys.crawl_dedup_key(project_id, self._namespace)

    def _deleted_fence_key(self, project_id: str) -> str:
        return crawl_keys.crawl_project_deleted_key(project_id, self._namespace)

    async def enqueue(
        self,
        project_id: str,
        tasks: list[QueueTask],
        priority: int = 5,
    ) -> list[str]:
        """任务入队

        Requirements: 1.4
        """
        if not tasks:
            return []

        stream_key = self._stream_key(project_id, priority)

        # 构建消息数据
        messages = []
        for task in tasks:
            task.priority = priority
            task.project_id = project_id
            messages.append(task.to_dict())

        # 批量入队
        msg_ids = await self._stream_client.xadd_batch_active(
            stream_key,
            messages,
            deleted_fence_key=self._deleted_fence_key(project_id),
        )

        # 更新任务的 msg_id
        for task, msg_id in zip(tasks, msg_ids, strict=False):
            task.msg_id = msg_id

        if msg_ids:
            logger.debug(f"入队成功: project={project_id}, priority={priority}, count={len(msg_ids)}")

        return msg_ids

    async def enqueue_unique(
        self,
        project_id: str,
        tasks: list[QueueTask],
        *,
        fingerprints: list[str],
        priority: int = 5,
    ) -> list[str | None]:
        if len(tasks) != len(fingerprints):
            raise ValueError("tasks 与 fingerprints 数量不一致")
        stream_key = self._stream_key(project_id, priority)
        dedup_key = self._dedup_key(project_id)
        results: list[str | None] = []
        for task, fingerprint in zip(tasks, fingerprints, strict=True):
            data = {**task.to_dict(), "priority": priority, "project_id": project_id}
            results.append(
                await self._stream_client.xadd_unique(
                    stream_key,
                    dedup_key,
                    deleted_fence_key=self._deleted_fence_key(project_id),
                    fingerprint=fingerprint,
                    data=data,
                )
            )
        return results

    async def dequeue(
        self,
        project_id: str,
        consumer: str,
        count: int = 50,
        timeout_ms: int = 5000,
    ) -> list[QueueTask]:
        """任务出队

        按优先级顺序获取任务。

        Requirements: 1.5
        """
        tasks = []
        remaining = count

        # 按优先级顺序遍历
        for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
            if remaining <= 0:
                break

            stream_key = self._stream_key(project_id, priority)

            # 确保消费者组存在
            await self._stream_client.ensure_active_group(
                stream_key,
                self._consumer_group,
                deleted_fence_key=self._deleted_fence_key(project_id),
            )

            # 从队列读取
            # 只在最低优先级队列使用阻塞等待
            block = timeout_ms if priority == Priority.LOW else None

            messages = await self._stream_client.xreadgroup(
                stream_key,
                group_name=self._consumer_group,
                consumer_name=consumer,
                count=remaining,
                block_ms=block,
                active_fence_key=self._deleted_fence_key(project_id),
            )

            # 转换为 QueueTask
            for msg in messages:
                try:
                    task = QueueTask.from_dict(msg.data, msg.msg_id)
                except (TypeError, ValueError) as exc:
                    await self._dead_letter_invalid(
                        CrawlQueueLocation(project_id, priority, stream_key),
                        msg_id=msg.msg_id,
                        data=msg.data,
                        error=exc,
                    )
                    continue
                task.priority = priority
                task.project_id = project_id
                tasks.append(task)

            remaining -= len(messages)

        if tasks:
            logger.debug(f"出队成功: project={project_id}, consumer={consumer}, count={len(tasks)}")

        return tasks

    async def ack(
        self,
        project_id: str,
        msg_ids: list[str],
        priority: int,
    ) -> int:
        """确认任务完成

        Requirements: 1.6
        """
        if not msg_ids:
            return 0

        stream_key = self._stream_key(project_id, priority)
        await self._stream_client.ensure_active_group(
            stream_key,
            self._consumer_group,
            deleted_fence_key=self._deleted_fence_key(project_id),
        )
        total_acked = await self._stream_client.xack_delete(
            stream_key,
            msg_ids,
            group_name=self._consumer_group,
        )

        if total_acked:
            logger.debug(f"确认成功: project={project_id}, acked={total_acked}")

        return total_acked

    async def list_project_ids(self) -> list[str]:
        """从 Stream 键扫描所有需要恢复的项目。"""
        discovery = await self.discover_projects()
        if discovery.failures:
            raise RuntimeError("; ".join(discovery.failures))
        return list(discovery.project_ids)

    async def discover_projects(self) -> QueueProjectDiscovery:
        """逐项目隔离删除 fence，避免一个残留 Stream 阻断全局恢复。"""
        pattern = crawl_keys.crawl_stream_pattern(self._namespace)
        keys = await self._stream_client.scan_keys(pattern)
        project_ids = {_project_id_from_stream_key(key, self._namespace) for key in keys}
        active_projects: list[str] = []
        failures: list[str] = []
        for project_id in sorted(item for item in project_ids if item):
            if await self._stream_client.exists(self._deleted_fence_key(project_id)):
                failures.append(f"已删除 Crawl 项目仍存在 Stream: project={project_id}")
                continue
            active_projects.append(project_id)
        return QueueProjectDiscovery(tuple(active_projects), tuple(failures))
