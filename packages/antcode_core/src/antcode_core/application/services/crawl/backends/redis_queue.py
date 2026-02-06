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

from antcode_core.domain.models.enums import Priority
from antcode_core.application.services.crawl.backends.base import (
    CrawlQueueBackend,
    QueueMetrics,
    QueueStats,
    QueueTask,
    ReclaimedTask,
)
from antcode_core.infrastructure.redis.stream_client import StreamClient

# Redis 键前缀和后缀
STREAM_KEY_PREFIX = "rule"
STREAM_KEY_SUFFIX = "stream"
DEAD_LETTER_SUFFIX = "dead_letter"

# 默认配置
DEFAULT_CONSUMER_GROUP = "crawl_workers"
DEFAULT_STREAM_MAXLEN = 100000


def get_stream_key(project_id: str, priority: int) -> str:
    """获取指定优先级的 Stream 键名

    Args:
        project_id: 项目 ID
        priority: 优先级 (0=高, 5=普通, 9=低)

    Returns:
        Redis 键名，格式: rule:{project_id}:stream:{priority}
    """
    return f"{STREAM_KEY_PREFIX}:{project_id}:{STREAM_KEY_SUFFIX}:{priority}"


def get_dead_letter_key(project_id: str) -> str:
    """获取死信队列的 Redis 键名

    Args:
        project_id: 项目 ID

    Returns:
        Redis 键名，格式: rule:{project_id}:dead_letter
    """
    return f"{STREAM_KEY_PREFIX}:{project_id}:{DEAD_LETTER_SUFFIX}"


def get_all_priority_keys(project_id: str) -> list:
    """获取所有优先级的 Stream 键名（按优先级排序）

    Args:
        project_id: 项目 ID

    Returns:
        Stream 键名列表，按优先级从高到低排序
    """
    return [
        get_stream_key(project_id, Priority.HIGH),
        get_stream_key(project_id, Priority.NORMAL),
        get_stream_key(project_id, Priority.LOW),
    ]


class RedisCrawlQueueBackend(CrawlQueueBackend):
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

        stream_key = get_stream_key(project_id, priority)

        # 构建消息数据
        messages = []
        for task in tasks:
            task.priority = priority
            task.project_id = project_id
            messages.append(task.to_dict())

        # 批量入队
        msg_ids = await self._stream_client.xadd_batch(
            stream_key,
            messages,
            maxlen=self._max_stream_len,
        )

        # 更新任务的 msg_id
        for task, msg_id in zip(tasks, msg_ids, strict=False):
            task.msg_id = msg_id

        if msg_ids:
            logger.debug(
                f"入队成功: project={project_id}, priority={priority}, "
                f"count={len(msg_ids)}"
            )

        return msg_ids

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

            stream_key = get_stream_key(project_id, priority)

            # 确保消费者组存在
            await self._stream_client.ensure_group(
                stream_key, self._consumer_group
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
            )

            # 转换为 QueueTask
            for msg in messages:
                task = QueueTask.from_dict(msg.data, msg.msg_id)
                task.priority = priority
                task.project_id = project_id
                tasks.append(task)

            remaining -= len(messages)

        if tasks:
            logger.debug(
                f"出队成功: project={project_id}, consumer={consumer}, "
                f"count={len(tasks)}"
            )

        return tasks

    async def ack(
        self,
        project_id: str,
        msg_ids: list[str],
    ) -> int:
        """确认任务完成

        Requirements: 1.6
        """
        if not msg_ids:
            return 0

        total_acked = 0

        # 需要在所有优先级队列中尝试确认
        for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
            stream_key = get_stream_key(project_id, priority)

            count = await self._stream_client.xack(
                stream_key,
                msg_ids,
                group_name=self._consumer_group,
            )
            total_acked += count

        if total_acked:
            logger.debug(
                f"确认成功: project={project_id}, acked={total_acked}"
            )

        return total_acked

    async def reclaim(
        self,
        project_id: str,
        min_idle_ms: int = 300000,
        count: int = 100,
    ) -> list[ReclaimedTask]:
        """回收超时任务

        Requirements: 1.7
        """
        reclaimed = []
        remaining = count

        for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
            if remaining <= 0:
                break

            stream_key = get_stream_key(project_id, priority)

            # 使用 XAUTOCLAIM 自动转移超时任务
            next_id, messages, deleted_ids = await self._stream_client.xautoclaim(
                stream_key,
                group_name=self._consumer_group,
                consumer_name="reclaimer",
                min_idle_time_ms=min_idle_ms,
                count=remaining,
            )

            if deleted_ids:
                logger.debug(
                    f"发现已删除消息: project={project_id}, "
                    f"priority={priority}, deleted={len(deleted_ids)}"
                )

            # 获取每个消息的 delivery_count
            for msg in messages:
                task = QueueTask.from_dict(msg.data, msg.msg_id)
                task.priority = priority
                task.project_id = project_id

                # 获取 delivery_count
                pending_info = await self._stream_client.xpending_range(
                    stream_key,
                    group_name=self._consumer_group,
                    start=msg.msg_id,
                    end=msg.msg_id,
                    count=1,
                )

                delivery_count = 1
                if pending_info:
                    delivery_count = pending_info[0].delivery_count

                task.retry_count = delivery_count

                reclaimed.append(ReclaimedTask(
                    task=task,
                    delivery_count=delivery_count,
                ))

            remaining -= len(messages)

        if reclaimed:
            logger.info(
                f"回收超时任务: project={project_id}, count={len(reclaimed)}"
            )

        return reclaimed

    async def stats(self, project_id: str) -> QueueStats:
        """获取队列统计信息

        Requirements: 1.8
        """
        pending = 0
        processing = 0

        for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
            stream_key = get_stream_key(project_id, priority)

            # 队列长度
            length = await self._stream_client.xlen(stream_key)
            pending += length

            # 处理中数量
            pending_info = await self._stream_client.xpending(
                stream_key, group_name=self._consumer_group
            )
            processing += pending_info.get("pending_count", 0)

        dead_letter = await self.get_dead_letter_count(project_id)

        return QueueStats(
            pending=pending,
            processing=processing,
            total=pending + processing,
            dead_letter=dead_letter,
        )

    async def get_queue_metrics(
        self,
        project_id: str,
        priority: int,
    ) -> QueueMetrics:
        """获取单个优先级队列指标"""
        stream_key = get_stream_key(project_id, priority)
        stream_length = await self._stream_client.xlen(stream_key)
        pending_info = await self._stream_client.xpending(
            stream_key, group_name=self._consumer_group
        )

        return QueueMetrics(
            queue_length=stream_length,
            pending_count=pending_info.get("pending_count", 0),
            consumers=pending_info.get("consumers", {}),
        )

    async def ensure_queues(self, project_id: str) -> bool:
        """确保项目队列存在"""
        try:
            for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
                stream_key = get_stream_key(project_id, priority)
                await self._stream_client.ensure_group(
                    stream_key, self._consumer_group
                )

            # 确保死信队列存在
            dead_letter_key = get_dead_letter_key(project_id)
            await self._stream_client.ensure_group(
                dead_letter_key, self._consumer_group
            )

            logger.debug(f"确保队列存在: project={project_id}")
            return True

        except Exception as e:
            logger.error(f"确保队列存在失败: project={project_id}, 错误: {e}")
            raise

    async def clear_queues(self, project_id: str) -> bool:
        """清空项目队列"""
        try:
            for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
                stream_key = get_stream_key(project_id, priority)
                await self._stream_client.delete_stream(stream_key)

            # 清空死信队列
            dead_letter_key = get_dead_letter_key(project_id)
            await self._stream_client.delete_stream(dead_letter_key)

            logger.info(f"清空队列: project={project_id}")
            return True

        except Exception as e:
            logger.error(f"清空队列失败: project={project_id}, 错误: {e}")
            return False

    async def get_queue_length(
        self,
        project_id: str,
        priority: int | None = None,
    ) -> int:
        """获取队列长度"""
        if priority is not None:
            stream_key = get_stream_key(project_id, priority)
            return await self._stream_client.xlen(stream_key)

        total = 0
        for p in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
            stream_key = get_stream_key(project_id, p)
            total += await self._stream_client.xlen(stream_key)
        return total

    async def get_pending_count(
        self,
        project_id: str,
        priority: int | None = None,
    ) -> int:
        """获取处理中消息数量"""
        if priority is not None:
            stream_key = get_stream_key(project_id, priority)
            info = await self._stream_client.xpending(
                stream_key, group_name=self._consumer_group
            )
            return info.get("pending_count", 0)

        total = 0
        for p in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
            stream_key = get_stream_key(project_id, p)
            info = await self._stream_client.xpending(
                stream_key, group_name=self._consumer_group
            )
            total += info.get("pending_count", 0)
        return total

    async def move_to_dead_letter(
        self,
        project_id: str,
        tasks: list[QueueTask],
    ) -> int:
        """将任务移入死信队列"""
        if not tasks:
            return 0

        dead_letter_key = get_dead_letter_key(project_id)

        # 构建死信消息
        messages = []
        msg_ids_by_priority: dict[int, list[str]] = {}

        for task in tasks:
            task.status = "failed"
            data = task.to_dict()
            data["original_priority"] = task.priority
            data["dead_letter_reason"] = "max_retries_exceeded"
            messages.append(data)

            # 按优先级分组 msg_id
            if task.priority not in msg_ids_by_priority:
                msg_ids_by_priority[task.priority] = []
            if task.msg_id:
                msg_ids_by_priority[task.priority].append(task.msg_id)

        # 添加到死信队列
        await self._stream_client.xadd_batch(dead_letter_key, messages)

        # 确认原消息
        for priority, msg_ids in msg_ids_by_priority.items():
            if msg_ids:
                stream_key = get_stream_key(project_id, priority)
                await self._stream_client.xack(
                    stream_key,
                    msg_ids,
                    group_name=self._consumer_group,
                )

        logger.info(f"移入死信队列: project={project_id}, count={len(tasks)}")
        return len(tasks)

    async def get_dead_letter_count(self, project_id: str) -> int:
        """获取死信队列消息数量"""
        dead_letter_key = get_dead_letter_key(project_id)
        return await self._stream_client.xlen(dead_letter_key)
