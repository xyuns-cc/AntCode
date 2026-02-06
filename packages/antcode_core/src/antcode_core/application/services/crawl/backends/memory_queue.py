"""内存队列后端实现

基于 Python 数据结构实现的内存队列，支持：
- 多优先级队列
- 任务超时回收
- 重试计数

适用于单机开发和测试环境。

Requirements: 1.3, 1.4, 1.5, 1.6, 1.7, 1.8
"""

import asyncio
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from loguru import logger

from antcode_core.domain.models.enums import Priority
from antcode_core.application.services.crawl.backends.base import (
    CrawlQueueBackend,
    QueueMetrics,
    QueueStats,
    QueueTask,
    ReclaimedTask,
)


@dataclass
class PendingTask:
    """处理中的任务"""

    task: QueueTask
    consumer: str
    claimed_at: float
    delivery_count: int = 1


@dataclass
class PriorityQueue:
    """单优先级队列"""

    # 待处理任务队列 (FIFO)
    pending: list = field(default_factory=list)
    # 处理中的任务 {msg_id: PendingTask}
    processing: dict = field(default_factory=dict)


class InMemoryCrawlQueueBackend(CrawlQueueBackend):
    """内存队列后端实现

    使用 Python 数据结构实现多优先级队列：
    - 每个优先级使用独立的 list 作为 FIFO 队列
    - 处理中的任务存储在 dict 中，支持超时回收
    - 死信队列存储最终失败的任务

    Requirements: 1.3, 1.4, 1.5, 1.6, 1.7, 1.8
    """

    def __init__(self, max_queue_size: int = 100000):
        """初始化内存队列

        Args:
            max_queue_size: 每个队列最大长度
        """
        self._max_queue_size = max_queue_size
        # {project_id: {priority: PriorityQueue}}
        self._queues: dict[str, dict[int, PriorityQueue]] = defaultdict(
            lambda: {
                Priority.HIGH: PriorityQueue(),
                Priority.NORMAL: PriorityQueue(),
                Priority.LOW: PriorityQueue(),
            }
        )
        # 死信队列 {project_id: [QueueTask]}
        self._dead_letter: dict[str, list] = defaultdict(list)
        # 锁
        self._lock = asyncio.Lock()

    def _generate_msg_id(self) -> str:
        """生成消息 ID"""
        timestamp = int(time.time() * 1000)
        seq = uuid.uuid4().hex[:8]
        return f"{timestamp}-{seq}"

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

        async with self._lock:
            queue = self._queues[project_id][priority]
            msg_ids = []

            for task in tasks:
                # 检查队列容量
                if len(queue.pending) >= self._max_queue_size:
                    logger.warning(
                        f"队列已满: project={project_id}, priority={priority}"
                    )
                    break

                # 生成消息 ID
                msg_id = self._generate_msg_id()
                task.msg_id = msg_id
                task.priority = priority
                task.project_id = project_id

                queue.pending.append(task)
                msg_ids.append(msg_id)

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

        async with self._lock:
            # 按优先级顺序遍历
            for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
                if remaining <= 0:
                    break

                queue = self._queues[project_id][priority]

                # 从待处理队列获取任务
                while remaining > 0 and queue.pending:
                    task = queue.pending.pop(0)

                    # 移入处理中
                    delivery_count = max(1, task.retry_count + 1)
                    pending_task = PendingTask(
                        task=task,
                        consumer=consumer,
                        claimed_at=time.time(),
                        delivery_count=delivery_count,
                    )
                    queue.processing[task.msg_id] = pending_task

                    tasks.append(task)
                    remaining -= 1

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

        acked = 0

        async with self._lock:
            for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
                queue = self._queues[project_id][priority]

                for msg_id in msg_ids:
                    if msg_id in queue.processing:
                        del queue.processing[msg_id]
                        acked += 1

        if acked:
            logger.debug(
                f"确认成功: project={project_id}, acked={acked}"
            )

        return acked

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
        now = time.time()
        min_idle_sec = min_idle_ms / 1000.0

        async with self._lock:
            for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
                if len(reclaimed) >= count:
                    break

                queue = self._queues[project_id][priority]
                expired_ids = []

                # 查找超时任务
                for msg_id, pending in queue.processing.items():
                    if len(reclaimed) + len(expired_ids) >= count:
                        break

                    idle_time = now - pending.claimed_at
                    if idle_time >= min_idle_sec:
                        expired_ids.append(msg_id)

                # 回收超时任务
                for msg_id in expired_ids:
                    pending = queue.processing.pop(msg_id)
                    task = pending.task
                    task.retry_count = pending.delivery_count

                    # 重新放入待处理队列
                    queue.pending.append(task)

                    reclaimed.append(ReclaimedTask(
                        task=task,
                        delivery_count=pending.delivery_count,
                    ))

        if reclaimed:
            logger.info(
                f"回收超时任务: project={project_id}, count={len(reclaimed)}"
            )

        return reclaimed

    async def stats(self, project_id: str) -> QueueStats:
        """获取队列统计信息

        Requirements: 1.8
        """
        async with self._lock:
            pending = 0
            processing = 0

            for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
                queue = self._queues[project_id][priority]
                pending += len(queue.pending)
                processing += len(queue.processing)

            dead_letter = len(self._dead_letter[project_id])

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
        async with self._lock:
            queue = self._queues[project_id][priority]
            consumers = {}
            for pending in queue.processing.values():
                consumers[pending.consumer] = consumers.get(pending.consumer, 0) + 1

            return QueueMetrics(
                queue_length=len(queue.pending),
                pending_count=len(queue.processing),
                consumers=consumers,
            )

    async def ensure_queues(self, project_id: str) -> bool:
        """确保项目队列存在"""
        async with self._lock:
            # 访问 defaultdict 会自动创建
            _ = self._queues[project_id]
            return True

    async def clear_queues(self, project_id: str) -> bool:
        """清空项目队列"""
        async with self._lock:
            if project_id in self._queues:
                del self._queues[project_id]
            if project_id in self._dead_letter:
                del self._dead_letter[project_id]

            logger.info(f"清空队列: project={project_id}")
            return True

    async def get_queue_length(
        self,
        project_id: str,
        priority: int | None = None,
    ) -> int:
        """获取队列长度"""
        async with self._lock:
            if priority is not None:
                queue = self._queues[project_id][priority]
                return len(queue.pending)

            total = 0
            for p in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
                queue = self._queues[project_id][p]
                total += len(queue.pending)
            return total

    async def get_pending_count(
        self,
        project_id: str,
        priority: int | None = None,
    ) -> int:
        """获取处理中消息数量"""
        async with self._lock:
            if priority is not None:
                queue = self._queues[project_id][priority]
                return len(queue.processing)

            total = 0
            for p in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
                queue = self._queues[project_id][p]
                total += len(queue.processing)
            return total

    async def move_to_dead_letter(
        self,
        project_id: str,
        tasks: list[QueueTask],
    ) -> int:
        """将任务移入死信队列"""
        if not tasks:
            return 0

        async with self._lock:
            for task in tasks:
                task.status = "failed"
                self._dead_letter[project_id].append(task)

                # 从处理中移除
                for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
                    queue = self._queues[project_id][priority]
                    if task.msg_id in queue.processing:
                        del queue.processing[task.msg_id]
                        break

            logger.info(
                f"移入死信队列: project={project_id}, count={len(tasks)}"
            )
            return len(tasks)

    async def get_dead_letter_count(self, project_id: str) -> int:
        """获取死信队列消息数量"""
        async with self._lock:
            return len(self._dead_letter[project_id])
