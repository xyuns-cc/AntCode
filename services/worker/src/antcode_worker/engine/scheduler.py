"""
本地调度器

实现本地优先级队列，支持 backpressure 和 fairness/aging。

Requirements: 4.2
"""

import asyncio
import contextlib
import heapq
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass(order=True)
class QueueItem:
    """队列项（支持优先级排序）"""
    priority: int                          # 负数，越小优先级越高
    enqueue_time: float                    # 入队时间（用于 aging）
    run_id: str = field(compare=False)
    data: Any = field(compare=False)
    cancelled: bool = field(default=False, compare=False)


class Scheduler:
    """
    本地调度器

    特性：
    - 优先级队列
    - Backpressure（队列满时阻塞）
    - Fairness/Aging（防止饥饿）

    Requirements: 4.2
    """

    def __init__(
        self,
        max_queue_size: int = 100,
        aging_interval: float = 60.0,      # 每 60 秒提升一次优先级
        aging_boost: int = 1,              # 每次提升的优先级
    ):
        self._max_size = max_queue_size
        self._aging_interval = aging_interval
        self._aging_boost = aging_boost

        self._queue: list[QueueItem] = []
        self._item_map: dict[str, QueueItem] = {}
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Condition(self._lock)
        self._not_full = asyncio.Condition(self._lock)

        self._running = False
        self._aging_task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动调度器"""
        self._running = True
        self._aging_task = asyncio.create_task(self._aging_loop())
        logger.info(f"调度器已启动 (max_size={self._max_size})")

    async def stop(self) -> None:
        """停止调度器"""
        self._running = False
        async with self._lock:
            self._not_empty.notify_all()
            self._not_full.notify_all()
        if self._aging_task:
            self._aging_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._aging_task
        logger.info("调度器已停止")

    async def enqueue(
        self,
        run_id: str,
        data: Any,
        priority: int = 0,
        timeout: float | None = None,
    ) -> bool:
        """
        入队

        Args:
            run_id: 运行 ID
            data: 任务数据
            priority: 优先级（越大越高）
            timeout: 超时时间（秒），None 表示无限等待

        Returns:
            是否成功入队
        """
        async with self._not_full:
            # 等待队列有空间
            while len(self._item_map) >= self._max_size:
                if not self._running:
                    return False
                try:
                    await asyncio.wait_for(
                        self._not_full.wait(),
                        timeout=timeout,
                    )
                except TimeoutError:
                    logger.warning(f"入队超时: {run_id}")
                    return False

            existing = self._item_map.get(run_id)
            if existing:
                existing.cancelled = True
                self._item_map.pop(run_id, None)
                logger.warning(f"入队覆盖已存在任务: {run_id}")

            # 入队（优先级取负数，heapq 是最小堆）
            item = QueueItem(
                priority=-priority,
                enqueue_time=time.time(),
                run_id=run_id,
                data=data,
            )
            heapq.heappush(self._queue, item)
            self._item_map[run_id] = item
            logger.debug(f"入队: {run_id} (priority={priority}, size={len(self._queue)})")

            # 通知消费者
            self._not_empty.notify()

        return True

    async def dequeue(self, timeout: float | None = None) -> tuple[str, Any] | None:
        """
        出队

        Args:
            timeout: 超时时间（秒），None 表示无限等待

        Returns:
            (run_id, data) 或 None
        """
        async with self._not_empty:
            while True:
                # 等待队列有数据
                while not self._item_map:
                    if not self._running:
                        return None
                    try:
                        await asyncio.wait_for(
                            self._not_empty.wait(),
                            timeout=timeout,
                        )
                    except TimeoutError:
                        return None

                if not self._item_map:
                    return None

                item = self._pop_next_item_locked()
                if item is None:
                    self._not_full.notify_all()
                    continue

                logger.debug(f"出队: {item.run_id} (size={len(self._queue)})")

                # 通知生产者
                self._not_full.notify()

                return (item.run_id, item.data)

    async def update_max_size(self, new_size: int) -> None:
        """更新队列最大容量"""
        async with self._not_full:
            self._max_size = max(1, int(new_size))
            self._not_full.notify_all()

    async def remove(self, run_id: str) -> bool:
        """移除指定任务"""
        async with self._not_full:
            item = self._item_map.pop(run_id, None)
            if item:
                item.cancelled = True
                logger.debug(f"移除: {run_id}")
                self._not_full.notify()
                return True
        return False

    async def _aging_loop(self) -> None:
        """Aging 循环，防止低优先级任务饥饿"""
        while self._running:
            try:
                await asyncio.sleep(self._aging_interval)
                if not self._running:
                    break

                async with self._lock:
                    now = time.time()
                    for item in self._queue:
                        if item.cancelled or item.run_id not in self._item_map:
                            continue
                        age = now - item.enqueue_time
                        if age > self._aging_interval:
                            # 提升优先级
                            item.priority -= self._aging_boost
                    heapq.heapify(self._queue)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Aging 循环异常: {e}")

    @property
    def size(self) -> int:
        """队列大小"""
        return len(self._item_map)

    @property
    def is_full(self) -> bool:
        """队列是否已满"""
        return len(self._item_map) >= self._max_size

    @property
    def is_empty(self) -> bool:
        """队列是否为空"""
        return len(self._item_map) == 0

    def _pop_next_item_locked(self) -> QueueItem | None:
        """弹出下一个有效任务（需已持有锁）"""
        while self._queue:
            item = heapq.heappop(self._queue)
            active = self._item_map.pop(item.run_id, None)
            if not active or item.cancelled:
                continue
            return item
        return None
