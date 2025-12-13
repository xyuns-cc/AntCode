"""内存任务队列实现

基于 heapq 的优先级队列，实现 TaskQueueBackend 接口。
适用于单 Master 节点部署场景。
"""
from __future__ import annotations

import asyncio
import heapq
from dataclasses import dataclass, field
from time import time
from typing import Dict, Any, Optional, List

from src.services.scheduler.queue_backend import BaseQueueBackend, QueuedTask


@dataclass(order=True)
class PriorityItem:
    """优先级队列项
    
    使用 (priority, enqueue_time, task_id) 作为排序键，
    确保相同优先级时按入队时间排序。
    """
    priority: int
    enqueue_time: float
    task_id: str = field(compare=False)
    task: QueuedTask = field(compare=False)


class MemoryQueueBackend(BaseQueueBackend):
    """内存任务队列
    
    基于 heapq 实现的优先级队列，支持 TaskQueueBackend 接口。
    优先级数值越小，优先级越高（先出队）。
    """

    BACKEND_TYPE = "memory"

    def __init__(self):
        super().__init__()
        self._heap: List[PriorityItem] = []
        self._task_map: Dict[str, PriorityItem] = {}  # task_id -> PriorityItem
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """启动队列"""
        self._running = True
        self._log_operation("启动", "N/A")

    async def stop(self) -> None:
        """停止队列"""
        self._running = False
        self._log_operation("停止", "N/A")

    async def enqueue(
        self,
        task_id: str,
        project_id: str,
        priority: int,
        data: Dict[str, Any],
        project_type: str = "rule"
    ) -> bool:
        """入队任务"""
        async with self._lock:
            # 检查是否已存在
            if task_id in self._task_map:
                self._log_warning("任务已在队列中，拒绝重复入队", task_id)
                return False

            # 创建 QueuedTask
            queued_task = QueuedTask(
                task_id=task_id,
                project_id=project_id,
                project_type=project_type,
                priority=priority,
                enqueue_time=time(),
                data=data
            )

            # 创建优先级项
            item = PriorityItem(
                priority=priority,
                enqueue_time=queued_task.enqueue_time,
                task_id=task_id,
                task=queued_task
            )

            # 入堆
            heapq.heappush(self._heap, item)
            self._task_map[task_id] = item
            self._update_stats("enqueued")

            self._log_operation("入队", task_id, priority=priority)
            return True

    async def dequeue(self, timeout: Optional[float] = None) -> Optional[QueuedTask]:
        """出队任务"""
        async with self._lock:
            # 跳过已取消的任务（懒删除）
            while self._heap:
                item = self._heap[0]
                if item.task_id in self._task_map:
                    # 有效任务，出队
                    heapq.heappop(self._heap)
                    del self._task_map[item.task_id]
                    self._update_stats("dequeued")
                    self._log_operation("出队", item.task_id)
                    return item.task
                else:
                    # 已取消的任务，跳过
                    heapq.heappop(self._heap)

            return None

    async def cancel(self, task_id: str) -> bool:
        """取消任务（懒删除）"""
        async with self._lock:
            if task_id not in self._task_map:
                return False

            # 从映射中删除，堆中的项会在 dequeue 时被跳过
            del self._task_map[task_id]
            self._update_stats("cancelled")
            self._log_operation("取消", task_id)
            return True

    async def update_priority(self, task_id: str, new_priority: int) -> bool:
        """更新任务优先级
        
        通过删除旧项并插入新项实现。
        """
        async with self._lock:
            if task_id not in self._task_map:
                return False

            old_item = self._task_map[task_id]
            old_task = old_item.task
            old_priority = old_item.priority

            # 从映射中删除旧项（堆中的旧项会在 dequeue 时被跳过）
            del self._task_map[task_id]

            # 创建新的 QueuedTask，保留原有数据但更新优先级
            new_task = QueuedTask(
                task_id=old_task.task_id,
                project_id=old_task.project_id,
                project_type=old_task.project_type,
                priority=new_priority,
                enqueue_time=old_task.enqueue_time,  # 保留原入队时间
                data=old_task.data
            )

            # 创建新的优先级项
            new_item = PriorityItem(
                priority=new_priority,
                enqueue_time=new_task.enqueue_time,
                task_id=task_id,
                task=new_task
            )

            # 入堆
            heapq.heappush(self._heap, new_item)
            self._task_map[task_id] = new_item
            self._update_stats("priority_updates")

            self._log_operation("优先级更新", task_id, old_priority=old_priority, new_priority=new_priority)
            return True

    async def get_status(self) -> Dict[str, Any]:
        """获取队列状态"""
        async with self._lock:
            return {
                "backend_type": self.BACKEND_TYPE,
                "queue_depth": len(self._task_map),
                "heap_size": len(self._heap),
                "running": self._running,
                "stats": self.get_stats()
            }

    def contains(self, task_id: str) -> bool:
        """检查任务是否在队列中"""
        return task_id in self._task_map

    def size(self) -> int:
        """获取队列大小"""
        return len(self._task_map)

    async def peek(self) -> Optional[QueuedTask]:
        """查看队首任务（不出队）"""
        async with self._lock:
            while self._heap:
                item = self._heap[0]
                if item.task_id in self._task_map:
                    return item.task
                else:
                    # 清理已取消的任务
                    heapq.heappop(self._heap)
            return None

    async def clear(self) -> int:
        """清空队列，返回清除的任务数"""
        async with self._lock:
            count = len(self._task_map)
            self._heap.clear()
            self._task_map.clear()
            return count
