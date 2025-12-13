"""调度器 - 任务队列管理"""
import asyncio
import heapq
import os
import time

import ujson
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from loguru import logger

from .signals import Signal


class TaskPriority(int, Enum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    IDLE = 4


class ProjectType(str, Enum):
    FILE = "file"
    CODE = "code"
    RULE = "rule"


DEFAULT_PRIORITY_MAP = {
    ProjectType.RULE: TaskPriority.HIGH,
    ProjectType.CODE: TaskPriority.NORMAL,
    ProjectType.FILE: TaskPriority.NORMAL,
}


def get_default_priority(project_type):
    return DEFAULT_PRIORITY_MAP.get(project_type, TaskPriority.NORMAL)


@dataclass(order=True)
class PriorityTask:
    priority: int
    enqueue_time: float = field(compare=True)
    task_id: str = field(compare=False, default="")
    project_id: str = field(compare=False, default="")
    project_type: ProjectType = field(compare=False, default=ProjectType.CODE)
    data: dict = field(compare=False, default_factory=dict)

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "project_type": self.project_type.value,
            "priority": self.priority,
            "enqueue_time": self.enqueue_time,
            "data": self.data,
        }


    @classmethod
    def from_dict(cls, d):
        return cls(
            priority=d.get("priority", TaskPriority.NORMAL.value),
            enqueue_time=d.get("enqueue_time", 0.0),
            task_id=d.get("task_id", ""),
            project_id=d.get("project_id", ""),
            project_type=ProjectType(d.get("project_type", ProjectType.CODE.value)),
            data=d.get("data", {}),
        )


@dataclass
class QueueStatus:
    total_count: int
    by_priority: dict = field(default_factory=dict)
    by_project_type: dict = field(default_factory=dict)
    enqueue_count: int = 0
    dequeue_count: int = 0
    avg_wait_time_ms: float = 0.0

    def to_dict(self):
        return {
            "total_count": self.total_count,
            "by_priority": self.by_priority,
            "by_project_type": self.by_project_type,
            "enqueue_count": self.enqueue_count,
            "dequeue_count": self.dequeue_count,
            "avg_wait_time_ms": self.avg_wait_time_ms,
        }


@dataclass(order=True)
class ScheduledTask:
    priority: int
    scheduled_at: float = field(compare=True)
    task_id: str = field(compare=False, default="")
    data: dict = field(compare=False, default_factory=dict)
    retry_count: int = field(compare=False, default=0)
    max_retries: int = field(compare=False, default=3)

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "priority": self.priority,
            "scheduled_at": self.scheduled_at,
            "data": self.data,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            priority=d.get("priority", TaskPriority.NORMAL.value),
            scheduled_at=d.get("scheduled_at", time.time()),
            task_id=d["task_id"],
            data=d.get("data", {}),
            retry_count=d.get("retry_count", 0),
            max_retries=d.get("max_retries", 3),
        )


class Scheduler:
    """统一调度器"""

    def __init__(self, signals=None, max_queue_size=10000, persist_path=None):
        self.signals = signals
        self.max_queue_size = max_queue_size
        self.persist_path = persist_path
        self._queue = []
        self._task_ids = set()
        self._task_map = {}
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Condition()
        self._running = False
        self._stats = {"enqueue_count": 0, "dequeue_count": 0, "dropped_count": 0, "total_wait_time_ms": 0.0}

    async def start(self):
        self._running = True
        if self.persist_path:
            await self.restore()
        logger.info("调度器已启动")

    async def stop(self):
        self._running = False
        if self.persist_path:
            await self.persist()
        logger.info("调度器已停止")

    async def enqueue(self, task_id, project_id="", project_type=ProjectType.CODE, priority=None, data=None):
        async with self._lock:
            if task_id in self._task_ids:
                return False
            if len(self._queue) >= self.max_queue_size:
                self._stats["dropped_count"] += 1
                return False
            if priority is None:
                priority = get_default_priority(project_type).value
            task = PriorityTask(priority=priority, enqueue_time=time.time(), task_id=task_id,
                               project_id=project_id, project_type=project_type, data=data or {})
            heapq.heappush(self._queue, task)
            self._task_ids.add(task_id)
            self._task_map[task_id] = task
            self._stats["enqueue_count"] += 1
        async with self._not_empty:
            self._not_empty.notify()
        if self.signals:
            await self.signals.send_catch_log(Signal.TASK_SCHEDULED, sender=self, task_id=task_id, priority=priority)
        return True

    async def dequeue(self, timeout=None):
        async with self._not_empty:
            while not self._queue and self._running:
                try:
                    await asyncio.wait_for(self._not_empty.wait(), timeout=timeout or 1.0)
                except asyncio.TimeoutError:
                    if timeout:
                        return None
                    continue
            if not self._queue:
                return None
            async with self._lock:
                if not self._queue:
                    return None
                task = heapq.heappop(self._queue)
                self._task_ids.discard(task.task_id)
                self._task_map.pop(task.task_id, None)
                self._stats["dequeue_count"] += 1
                self._stats["total_wait_time_ms"] += (time.time() - task.enqueue_time) * 1000
                return task


    async def cancel(self, task_id):
        async with self._lock:
            if task_id not in self._task_ids:
                return False
            self._queue = [t for t in self._queue if t.task_id != task_id]
            heapq.heapify(self._queue)
            self._task_ids.discard(task_id)
            self._task_map.pop(task_id, None)
            return True

    async def update_priority(self, task_id, new_priority):
        async with self._lock:
            if task_id not in self._task_ids:
                return None
            old_task = self._task_map.get(task_id)
            if not old_task:
                return None
            new_task = PriorityTask(priority=new_priority, enqueue_time=old_task.enqueue_time,
                                   task_id=old_task.task_id, project_id=old_task.project_id,
                                   project_type=old_task.project_type, data=old_task.data)
            self._queue = [t for t in self._queue if t.task_id != task_id]
            self._queue.append(new_task)
            heapq.heapify(self._queue)
            self._task_map[task_id] = new_task
            for i, t in enumerate(sorted(self._queue)):
                if t.task_id == task_id:
                    return i
            return -1

    def get_status(self):
        by_priority = {}
        by_project_type = {}
        for task in self._queue:
            by_priority[task.priority] = by_priority.get(task.priority, 0) + 1
            pt = task.project_type.value
            by_project_type[pt] = by_project_type.get(pt, 0) + 1
        avg_wait = self._stats["total_wait_time_ms"] / self._stats["dequeue_count"] if self._stats["dequeue_count"] > 0 else 0.0
        return QueueStatus(total_count=len(self._queue), by_priority=by_priority, by_project_type=by_project_type,
                          enqueue_count=self._stats["enqueue_count"], dequeue_count=self._stats["dequeue_count"], avg_wait_time_ms=avg_wait)

    def get_details(self):
        return [t.to_dict() for t in sorted(self._queue)]

    @property
    def size(self):
        return len(self._queue)

    @property
    def priority_size(self):
        return len(self._queue)

    def contains(self, task_id):
        return task_id in self._task_ids

    def get_stats(self):
        return {**self._stats, "queue_size": len(self._queue), "max_queue_size": self.max_queue_size}

    async def persist(self):
        if not self.persist_path:
            return
        try:
            data = {"version": 1, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "tasks": [t.to_dict() for t in self._queue], "stats": self._stats}
            os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
            with open(self.persist_path, "w", encoding="utf-8") as f:
                ujson.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"持久化失败: {e}")

    async def restore(self):
        if not self.persist_path or not os.path.exists(self.persist_path):
            return 0
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                data = ujson.load(f)
            for task_dict in data.get("tasks", []):
                task = PriorityTask.from_dict(task_dict)
                if task.task_id not in self._task_ids:
                    heapq.heappush(self._queue, task)
                    self._task_ids.add(task.task_id)
                    self._task_map[task.task_id] = task
            self._stats.update(data.get("stats", {}))
            logger.info(f"队列已恢复: {len(self._queue)} 任务")
            return len(self._queue)
        except Exception as e:
            logger.error(f"恢复失败: {e}")
            return 0


PriorityScheduler = Scheduler


@dataclass
class TaskItem:
    task_id: str
    project_id: str
    project_type: ProjectType
    priority: int = None
    params: dict = field(default_factory=dict)
    environment: dict = field(default_factory=dict)
    timeout: int = 3600
    download_url: str = None
    api_key: str = None
    file_hash: str = None
    entry_point: str = None

    def to_dict(self):
        return {"task_id": self.task_id, "project_id": self.project_id, "project_type": self.project_type.value,
                "priority": self.priority, "params": self.params, "environment": self.environment,
                "timeout": self.timeout, "download_url": self.download_url, "api_key": self.api_key, 
                "file_hash": self.file_hash, "entry_point": self.entry_point}

    @classmethod
    def from_dict(cls, d):
        return cls(task_id=d.get("task_id", ""), project_id=d.get("project_id", ""),
                  project_type=ProjectType(d.get("project_type", ProjectType.CODE.value)),
                  priority=d.get("priority"), params=d.get("params", {}), environment=d.get("environment", {}),
                  timeout=d.get("timeout", 3600), download_url=d.get("download_url"),
                  api_key=d.get("api_key"), file_hash=d.get("file_hash"), entry_point=d.get("entry_point"))


@dataclass
class BatchTaskRequest:
    tasks: list
    node_id: str
    batch_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())

    def to_dict(self):
        return {"tasks": [t.to_dict() for t in self.tasks], "node_id": self.node_id,
                "batch_id": self.batch_id, "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, d):
        return cls(tasks=[TaskItem.from_dict(t) for t in d.get("tasks", [])], node_id=d.get("node_id", ""),
                  batch_id=d.get("batch_id", str(uuid.uuid4())), timestamp=d.get("timestamp", datetime.now().timestamp()))


@dataclass
class BatchTaskResponse:
    batch_id: str
    accepted_count: int
    rejected_count: int
    accepted_tasks: list = field(default_factory=list)
    rejected_tasks: list = field(default_factory=list)

    def to_dict(self):
        return {"batch_id": self.batch_id, "accepted_count": self.accepted_count,
                "rejected_count": self.rejected_count, "accepted_tasks": self.accepted_tasks,
                "rejected_tasks": self.rejected_tasks}


class BatchReceiver:
    def __init__(self, scheduler):
        self.scheduler = scheduler

    async def receive_batch(self, request):
        accepted_tasks = []
        rejected_tasks = []
        for task in request.tasks:
            is_valid, error = self._validate_task(task)
            if not is_valid:
                rejected_tasks.append({"task_id": task.task_id or "unknown", "reason": error or "Unknown error"})
                continue
            priority = task.priority if task.priority is not None else get_default_priority(task.project_type).value
            success = await self.scheduler.enqueue(task_id=task.task_id, project_id=task.project_id,
                                                   project_type=task.project_type, priority=priority,
                                                   data={"params": task.params, "environment": task.environment,
                                                        "timeout": task.timeout, "download_url": task.download_url,
                                                        "api_key": task.api_key, "file_hash": task.file_hash,
                                                        "entry_point": task.entry_point})
            if success:
                accepted_tasks.append(task.task_id)
            else:
                rejected_tasks.append({"task_id": task.task_id, "reason": "Task already exists or queue full"})
        logger.info(f"批量任务接收完成 [batch_id={request.batch_id}]: 接受 {len(accepted_tasks)}, 拒绝 {len(rejected_tasks)}")
        return BatchTaskResponse(batch_id=request.batch_id, accepted_count=len(accepted_tasks),
                                rejected_count=len(rejected_tasks), accepted_tasks=accepted_tasks, rejected_tasks=rejected_tasks)

    def _validate_task(self, task):
        if not task.task_id:
            return False, "Missing task_id"
        if not task.project_id:
            return False, "Missing project_id"
        try:
            if not isinstance(task.project_type, ProjectType):
                ProjectType(task.project_type)
        except (ValueError, TypeError):
            return False, f"Invalid project_type: {task.project_type}"
        if task.priority is not None and not (0 <= task.priority <= 4):
            return False, f"Invalid priority: {task.priority}"
        return True, None
