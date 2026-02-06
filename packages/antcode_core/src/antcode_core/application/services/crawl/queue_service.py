"""多优先级队列服务

基于抽象后端实现多优先级任务队列，支持：
- 多优先级队列入队和出队
- 任务确认和超时回收
- 死信队列处理
- 重试机制

通过 CRAWL_BACKEND 环境变量配置后端类型：
- "memory": 内存队列（默认，适用于开发测试）
- "redis": Redis Streams（适用于生产环境）

Requirements: 1.9, 2.1, 2.2, 2.3, 4.1, 4.2, 4.3, 11.1, 11.2, 11.3, 11.4, 11.5
"""

from dataclasses import dataclass, field

from loguru import logger

from antcode_core.common.exceptions import CrawlError
from antcode_core.domain.models.enums import Priority, TaskStatus
from antcode_core.application.services.base import BaseService
from antcode_core.application.services.crawl.backends import (
    CrawlQueueBackend,
    QueueTask,
    get_queue_backend,
)
from antcode_core.application.services.crawl.dedup_service import CrawlDedupService, crawl_dedup_service

# 默认配置
DEFAULT_TASK_TIMEOUT_MS = 300000  # 5 分钟
DEFAULT_MAX_RETRIES = 3
DEFAULT_BATCH_SIZE = 50


@dataclass
class CrawlTask:
    """爬取任务数据类

    Requirements: 8.1-8.7 - 任务状态管理
    """

    msg_id: str = ""
    url: str = ""
    method: str = "GET"
    headers: dict = field(default_factory=dict)
    depth: int = 0
    priority: int = Priority.NORMAL
    retry_count: int = 0
    parent_url: str | None = None
    batch_id: str = ""
    project_id: str = ""
    status: str = TaskStatus.PENDING

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "url": self.url,
            "method": self.method,
            "headers": self.headers or {},
            "depth": self.depth,
            "priority": self.priority,
            "retry_count": self.retry_count,
            "parent_url": self.parent_url or "",
            "batch_id": self.batch_id,
            "project_id": self.project_id,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict, msg_id: str = "") -> "CrawlTask":
        """从字典创建任务"""
        return cls(
            msg_id=msg_id,
            url=data.get("url", ""),
            method=data.get("method", "GET"),
            headers=data.get("headers") or {},
            depth=int(data.get("depth", 0)),
            priority=int(data.get("priority", Priority.NORMAL)),
            retry_count=int(data.get("retry_count", 0)),
            parent_url=data.get("parent_url") or None,
            batch_id=data.get("batch_id", ""),
            project_id=data.get("project_id", ""),
            status=data.get("status", TaskStatus.PENDING),
        )

    @classmethod
    def from_queue_task(cls, qt: QueueTask) -> "CrawlTask":
        """从 QueueTask 创建"""
        return cls(
            msg_id=qt.msg_id,
            url=qt.url,
            method=qt.method,
            headers=qt.headers,
            depth=qt.depth,
            priority=qt.priority,
            retry_count=qt.retry_count,
            parent_url=qt.parent_url,
            batch_id=qt.batch_id,
            project_id=qt.project_id,
            status=qt.status,
        )

    def to_queue_task(self) -> QueueTask:
        """转换为 QueueTask"""
        return QueueTask(
            msg_id=self.msg_id,
            url=self.url,
            method=self.method,
            headers=self.headers,
            depth=self.depth,
            priority=self.priority,
            retry_count=self.retry_count,
            parent_url=self.parent_url,
            batch_id=self.batch_id,
            project_id=self.project_id,
            status=self.status,
        )


@dataclass
class EnqueueResult:
    """入队结果"""

    total: int = 0
    enqueued: int = 0
    duplicate: int = 0
    msg_ids: list = field(default_factory=list)


@dataclass
class DequeueResult:
    """出队结果"""

    tasks: list = field(default_factory=list)
    source_priority: int = -1


@dataclass
class TaskStatusTransition:
    """任务状态转换结果

    Requirements: 8.1-8.7 - 任务状态管理
    """

    success: bool = False
    task: CrawlTask | None = None
    from_status: str = ""
    to_status: str = ""
    error: str = ""


class TaskStatusError(CrawlError):
    """任务状态错误"""
    pass


class InvalidStatusTransitionError(TaskStatusError):
    """无效的状态转换"""
    pass


class CrawlQueueService(BaseService):
    """多优先级队列服务

    基于抽象后端实现高性能任务队列，支持：
    - 多优先级队列（高/普通/低）
    - 任务去重（集成 Bloom Filter）
    - 任务确认和超时回收
    - 死信队列处理
    - 重试机制

    Requirements: 1.9, 2.1, 2.2, 2.3, 4.1, 4.2, 4.3, 11.1, 11.2, 11.3, 11.4, 11.5
    """

    def __init__(
        self,
        backend: CrawlQueueBackend = None,
        dedup_service: CrawlDedupService = None,
        task_timeout_ms: int = None,
        max_retries: int = None,
    ):
        """初始化队列服务

        Args:
            backend: 队列后端，为 None 时通过工厂方法获取
            dedup_service: 去重服务，为 None 时使用全局实例
            task_timeout_ms: 任务超时时间（毫秒）
            max_retries: 最大重试次数
        """
        super().__init__()
        self._backend = backend or get_queue_backend()
        self._dedup_service = dedup_service or crawl_dedup_service
        self._task_timeout_ms = task_timeout_ms or DEFAULT_TASK_TIMEOUT_MS
        self._max_retries = max_retries or DEFAULT_MAX_RETRIES

    # =========================================================================
    # 初始化和配置
    # =========================================================================

    async def ensure_queues(self, project_id: str) -> bool:
        """确保项目的所有优先级队列存在

        Args:
            project_id: 项目 ID

        Returns:
            是否成功
        """
        return await self._backend.ensure_queues(project_id)

    # =========================================================================
    # 入队操作
    # =========================================================================

    async def enqueue_url(
        self,
        project_id: str,
        url: str,
        batch_id: str = "",
        priority: int = Priority.NORMAL,
        depth: int = 0,
        parent_url: str = None,
        method: str = "GET",
        headers: dict = None,
        skip_dedup: bool = False,
    ) -> tuple:
        """单个 URL 入队

        Args:
            project_id: 项目 ID
            url: 目标 URL
            batch_id: 批次 ID
            priority: 优先级
            depth: 当前深度
            parent_url: 父 URL
            method: HTTP 方法
            headers: 请求头
            skip_dedup: 是否跳过去重检查

        Returns:
            (success, msg_id, is_duplicate) 元组

        Requirements: 2.3, 11.1
        """
        # 去重检查
        if not skip_dedup:
            is_duplicate = await self._dedup_service.exists(project_id, url)
            if is_duplicate:
                logger.debug(f"URL 已存在，跳过入队: project={project_id}, url={url[:50]}...")
                return False, "", True

            # 添加到去重过滤器
            await self._dedup_service.add(project_id, url)

        # 构建任务
        task = QueueTask(
            url=url,
            method=method,
            headers=headers or {},
            depth=depth,
            priority=priority,
            retry_count=0,
            parent_url=parent_url,
            batch_id=batch_id,
            project_id=project_id,
        )

        # 入队
        msg_ids = await self._backend.enqueue(project_id, [task], priority)

        if msg_ids:
            logger.debug(f"URL 入队成功: project={project_id}, priority={priority}, "
                         f"url={url[:50]}..., msg_id={msg_ids[0]}")
            return True, msg_ids[0], False

        return False, "", False

    async def enqueue_urls(
        self,
        project_id: str,
        urls: list,
        batch_id: str = "",
        priority: int = Priority.NORMAL,
        depth: int = 0,
        parent_url: str = None,
        method: str = "GET",
        headers: dict = None,
        skip_dedup: bool = False,
    ) -> EnqueueResult:
        """批量 URL 入队

        Args:
            project_id: 项目 ID
            urls: URL 列表
            batch_id: 批次 ID
            priority: 优先级
            depth: 当前深度
            parent_url: 父 URL
            method: HTTP 方法
            headers: 请求头
            skip_dedup: 是否跳过去重检查

        Returns:
            EnqueueResult 对象

        Requirements: 2.3, 11.1
        """
        if not urls:
            return EnqueueResult()

        result = EnqueueResult(total=len(urls))

        # 去重过滤（使用批量方法避免 N+1 查询）
        urls_to_enqueue = urls
        if not skip_dedup:
            urls_to_enqueue, _, _ = await self._dedup_service.filter_and_add_new_urls(
                project_id, urls
            )

        if not urls_to_enqueue:
            result.duplicate = len(urls)
            return result

        # 构建任务列表
        tasks = []
        for url in urls_to_enqueue:
            task = QueueTask(
                url=url,
                method=method,
                headers=headers or {},
                depth=depth,
                priority=priority,
                retry_count=0,
                parent_url=parent_url,
                batch_id=batch_id,
                project_id=project_id,
            )
            tasks.append(task)

        # 批量入队
        msg_ids = await self._backend.enqueue(project_id, tasks, priority)

        result.enqueued = len(msg_ids)
        result.duplicate = len(urls) - len(msg_ids)
        result.msg_ids = msg_ids

        logger.info(f"批量 URL 入队: project={project_id}, priority={priority}, "
                    f"total={result.total}, enqueued={result.enqueued}, "
                    f"duplicate={result.duplicate}")

        return result

    # =========================================================================
    # 出队操作
    # =========================================================================

    async def fetch_tasks(
        self,
        project_id: str,
        worker_id: str,
        count: int = DEFAULT_BATCH_SIZE,
        block_ms: int = None,
    ) -> list:
        """获取任务（按优先级）

        从多优先级队列中获取任务，优先返回高优先级队列中的任务。
        获取的任务状态自动设置为 DISPATCHED。

        Args:
            project_id: 项目 ID
            worker_id: Worker ID
            count: 获取数量
            block_ms: 阻塞等待毫秒数，None 表示不阻塞

        Returns:
            CrawlTask 列表

        Requirements: 2.1, 8.2, 11.2, 11.3, 11.4
        """
        queue_tasks = await self._backend.dequeue(
            project_id,
            consumer=worker_id,
            count=count,
            timeout_ms=block_ms or 5000,
        )

        # 转换为 CrawlTask 并设置状态
        tasks = []
        for qt in queue_tasks:
            task = CrawlTask.from_queue_task(qt)
            task.status = TaskStatus.DISPATCHED
            tasks.append(task)

        if tasks:
            logger.info(f"获取任务完成: project={project_id}, worker={worker_id}, "
                        f"total={len(tasks)}")

        return tasks

    # =========================================================================
    # 任务确认
    # =========================================================================

    async def ack_task(self, project_id: str, msg_id: str, priority: int = None) -> bool:
        """确认单个任务完成

        Args:
            project_id: 项目 ID
            msg_id: 消息 ID
            priority: 优先级（可选，后端会在所有队列中查找）

        Returns:
            是否确认成功

        Requirements: 2.2
        """
        count = await self._backend.ack(project_id, [msg_id])

        if count > 0:
            logger.debug(f"确认任务完成: project={project_id}, msg_id={msg_id}")
            return True

        logger.warning(f"确认任务失败: project={project_id}, msg_id={msg_id}")
        return False

    async def ack_tasks(self, project_id: str, msg_ids: list, priority: int = None) -> int:
        """批量确认任务完成

        Args:
            project_id: 项目 ID
            msg_ids: 消息 ID 列表
            priority: 优先级（可选）

        Returns:
            确认成功的数量

        Requirements: 2.2
        """
        if not msg_ids:
            return 0

        count = await self._backend.ack(project_id, msg_ids)

        logger.info(f"批量确认任务完成: project={project_id}, "
                    f"requested={len(msg_ids)}, acked={count}")

        return count

    async def ack_tasks_multi_priority(
        self,
        project_id: str,
        tasks: list,
    ) -> int:
        """批量确认不同优先级的任务

        Args:
            project_id: 项目 ID
            tasks: CrawlTask 列表（包含 msg_id）

        Returns:
            确认成功的总数量
        """
        if not tasks:
            return 0

        msg_ids = [task.msg_id for task in tasks if task.msg_id]
        return await self._backend.ack(project_id, msg_ids)

    # =========================================================================
    # 超时回收
    # =========================================================================

    async def reclaim_timeout_tasks(
        self,
        project_id: str,
        timeout_ms: int = None,
        count: int = 100,
    ) -> list:
        """回收所有优先级队列中的超时任务

        扫描处理中的任务，将超时任务回收。
        如果任务重试次数超过最大限制，则移入死信队列。

        Args:
            project_id: 项目 ID
            timeout_ms: 超时时间（毫秒），默认使用配置值
            count: 最大回收数量

        Returns:
            回收的 CrawlTask 列表

        Requirements: 4.1, 4.2, 4.3
        """
        timeout_ms = timeout_ms or self._task_timeout_ms

        reclaimed_items = await self._backend.reclaim(
            project_id,
            min_idle_ms=timeout_ms,
            count=count,
        )

        reclaimed_tasks = []
        dead_letter_tasks = []

        for item in reclaimed_items:
            task = CrawlTask.from_queue_task(item.task)
            task.status = TaskStatus.TIMEOUT

            # 检查是否超过最大重试次数
            if item.delivery_count > self._max_retries:
                task.status = TaskStatus.FAILED
                dead_letter_tasks.append(task)
                logger.warning(f"任务超过最大重试次数，移入死信队列: "
                               f"project={project_id}, msg_id={task.msg_id}, "
                               f"url={task.url[:50]}..., retries={item.delivery_count}")
            else:
                task.retry_count = item.delivery_count
                reclaimed_tasks.append(task)

        # 处理死信任务
        if dead_letter_tasks:
            queue_tasks = [t.to_queue_task() for t in dead_letter_tasks]
            await self._backend.move_to_dead_letter(project_id, queue_tasks)

        if reclaimed_tasks:
            logger.info(f"回收超时任务: project={project_id}, total={len(reclaimed_tasks)}")

        return reclaimed_tasks

    async def retry_task(
        self,
        project_id: str,
        task: CrawlTask,
    ) -> tuple:
        """重试任务（保持原有优先级）

        Args:
            project_id: 项目 ID
            task: 要重试的任务

        Returns:
            (success, new_msg_id) 元组

        Requirements: 11.5
        """
        # 增加重试计数
        task.retry_count += 1

        # 检查是否超过最大重试次数
        if task.retry_count > self._max_retries:
            queue_task = task.to_queue_task()
            await self._backend.move_to_dead_letter(project_id, [queue_task])
            return False, ""

        # 重新入队（保持原有优先级）
        queue_task = task.to_queue_task()
        msg_ids = await self._backend.enqueue(project_id, [queue_task], task.priority)

        if msg_ids:
            logger.debug(f"任务重试入队: project={project_id}, priority={task.priority}, "
                         f"url={task.url[:50]}..., retry_count={task.retry_count}, "
                         f"new_msg_id={msg_ids[0]}")
            return True, msg_ids[0]

        return False, ""

    # =========================================================================
    # 队列信息查询
    # =========================================================================

    async def get_queue_length(self, project_id: str, priority: int = None) -> int:
        """获取队列长度

        Args:
            project_id: 项目 ID
            priority: 优先级，None 表示所有优先级的总和

        Returns:
            队列长度
        """
        return await self._backend.get_queue_length(project_id, priority)

    async def get_pending_count(self, project_id: str, priority: int = None) -> int:
        """获取待处理（处理中）消息数量

        Args:
            project_id: 项目 ID
            priority: 优先级，None 表示所有优先级的总和

        Returns:
            待处理消息数量
        """
        return await self._backend.get_pending_count(project_id, priority)

    async def get_dead_letter_count(self, project_id: str) -> int:
        """获取死信队列消息数量

        Args:
            project_id: 项目 ID

        Returns:
            死信队列消息数量
        """
        return await self._backend.get_dead_letter_count(project_id)

    async def get_queue_stats(self, project_id: str) -> dict:
        """获取队列统计信息

        Args:
            project_id: 项目 ID

        Returns:
            统计信息字典
        """
        stats = await self._backend.stats(project_id)

        return {
            "project_id": project_id,
            "total_length": stats.pending,
            "total_pending": stats.processing,
            "dead_letter_count": stats.dead_letter,
        }

    # =========================================================================
    # 清理操作
    # =========================================================================

    async def clear_queues(self, project_id: str) -> bool:
        """清空项目的所有队列

        Args:
            project_id: 项目 ID

        Returns:
            是否成功
        """
        return await self._backend.clear_queues(project_id)

    # =========================================================================
    # 任务状态管理
    # =========================================================================

    async def transition_task_status(
        self,
        task: CrawlTask,
        to_status: str,
        validate: bool = True,
    ) -> TaskStatusTransition:
        """转换任务状态

        实现状态机规则:
        - PENDING → DISPATCHED: 任务分发给 Worker
        - DISPATCHED → RUNNING: Worker 开始执行
        - RUNNING → SUCCESS: 任务执行成功
        - RUNNING → RETRY: 任务执行失败但可重试
        - RUNNING → TIMEOUT: 任务执行超时
        - RETRY → DISPATCHED: 重试任务重新分发
        - TIMEOUT → DISPATCHED: 超时任务重新分发
        - RETRY → FAILED: 重试次数超限
        - TIMEOUT → FAILED: 超时次数超限

        Args:
            task: 任务对象
            to_status: 目标状态
            validate: 是否验证状态转换有效性

        Returns:
            TaskStatusTransition 对象

        Requirements: 8.1-8.7
        """
        from_status = task.status
        result = TaskStatusTransition(
            from_status=from_status,
            to_status=to_status,
        )

        # 验证状态转换
        if validate and not TaskStatus.is_valid_transition(from_status, to_status):
            error_msg = f"无效的状态转换: {from_status} → {to_status}"
            logger.warning(f"任务状态转换失败: msg_id={task.msg_id}, {error_msg}")
            result.error = error_msg
            return result

        # 更新任务状态
        task.status = to_status
        result.task = task
        result.success = True

        logger.debug(f"任务状态转换: msg_id={task.msg_id}, "
                     f"{from_status} → {to_status}")

        return result

    async def dispatch_task(self, task: CrawlTask) -> TaskStatusTransition:
        """分发任务（PENDING → DISPATCHED）

        Requirements: 8.2
        """
        return await self.transition_task_status(task, TaskStatus.DISPATCHED)

    async def start_task(self, task: CrawlTask) -> TaskStatusTransition:
        """开始执行任务（DISPATCHED → RUNNING）

        Requirements: 8.3
        """
        return await self.transition_task_status(task, TaskStatus.RUNNING)

    async def complete_task_success(
        self,
        task: CrawlTask,
        auto_ack: bool = True,
    ) -> TaskStatusTransition:
        """任务执行成功（RUNNING → SUCCESS）

        Args:
            task: 任务对象
            auto_ack: 是否自动确认任务

        Returns:
            TaskStatusTransition 对象

        Requirements: 8.4
        """
        result = await self.transition_task_status(task, TaskStatus.SUCCESS)

        if result.success and auto_ack and task.msg_id:
            await self.ack_task(task.project_id, task.msg_id)
            logger.debug(f"任务成功并确认: msg_id={task.msg_id}")

        return result

    async def mark_task_retry(
        self,
        task: CrawlTask,
        auto_requeue: bool = True,
    ) -> TaskStatusTransition:
        """标记任务需要重试（RUNNING → RETRY）

        如果重试次数未超限，增加重试计数并重新入队。
        如果重试次数超限，移入死信队列并设置状态为 FAILED。

        Args:
            task: 任务对象
            auto_requeue: 是否自动重新入队

        Returns:
            TaskStatusTransition 对象

        Requirements: 8.5, 11.5
        """
        result = await self.transition_task_status(task, TaskStatus.RETRY)

        if not result.success:
            return result

        # 检查重试次数
        if task.retry_count >= self._max_retries:
            return await self.mark_task_failed(task)

        if auto_requeue:
            success, new_msg_id = await self.retry_task(task.project_id, task)
            if success:
                if task.msg_id:
                    await self.ack_task(task.project_id, task.msg_id)
                logger.debug(f"任务重试入队: old_msg_id={task.msg_id}, "
                             f"new_msg_id={new_msg_id}, retry_count={task.retry_count}")
            else:
                result.success = False
                result.error = "重试入队失败"

        return result

    async def mark_task_timeout(
        self,
        task: CrawlTask,
    ) -> TaskStatusTransition:
        """标记任务超时（RUNNING/DISPATCHED → TIMEOUT）

        Requirements: 8.6
        """
        if task.status not in [TaskStatus.RUNNING, TaskStatus.DISPATCHED]:
            return TaskStatusTransition(
                success=False,
                from_status=task.status,
                to_status=TaskStatus.TIMEOUT,
                error=f"无法从状态 {task.status} 转换为 TIMEOUT",
            )

        result = await self.transition_task_status(
            task, TaskStatus.TIMEOUT, validate=False
        )

        if result.success:
            logger.debug(f"任务标记为超时: msg_id={task.msg_id}")

        return result

    async def mark_task_failed(
        self,
        task: CrawlTask,
        move_to_dead_letter: bool = True,
    ) -> TaskStatusTransition:
        """标记任务最终失败（RETRY/TIMEOUT → FAILED）

        Requirements: 8.7
        """
        if task.status not in [TaskStatus.RETRY, TaskStatus.TIMEOUT, TaskStatus.RUNNING]:
            return TaskStatusTransition(
                success=False,
                from_status=task.status,
                to_status=TaskStatus.FAILED,
                error=f"无法从状态 {task.status} 转换为 FAILED",
            )

        result = await self.transition_task_status(
            task, TaskStatus.FAILED, validate=False
        )

        if result.success and move_to_dead_letter:
            queue_task = task.to_queue_task()
            await self._backend.move_to_dead_letter(task.project_id, [queue_task])
            logger.info(f"任务最终失败，移入死信队列: msg_id={task.msg_id}, "
                        f"url={task.url[:50]}...")

        return result

    async def process_task_result(
        self,
        task: CrawlTask,
        success: bool,
        error: str = None,
    ) -> TaskStatusTransition:
        """处理任务执行结果

        根据执行结果自动转换任务状态:
        - 成功: RUNNING → SUCCESS
        - 失败且可重试: RUNNING → RETRY → DISPATCHED
        - 失败且不可重试: RUNNING → FAILED

        Args:
            task: 任务对象
            success: 是否执行成功
            error: 错误信息（失败时）

        Returns:
            TaskStatusTransition 对象

        Requirements: 8.4, 8.5, 8.7
        """
        if success:
            return await self.complete_task_success(task)
        else:
            if task.retry_count < self._max_retries:
                return await self.mark_task_retry(task)
            else:
                return await self.mark_task_failed(task)


# 全局服务实例
crawl_queue_service = CrawlQueueService()
