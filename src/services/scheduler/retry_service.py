"""任务重试与补偿服务"""
import asyncio
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any, List, Callable
from loguru import logger

from src.models.enums import TaskStatus
from src.models.scheduler import ScheduledTask, TaskExecution


class RetryStrategy(str, Enum):
    """重试策略"""
    FIXED = "fixed"           # 固定间隔重试
    EXPONENTIAL = "exponential"  # 指数退避重试
    LINEAR = "linear"         # 线性增长重试
    CUSTOM = "custom"         # 自定义重试


class CompensationType(str, Enum):
    """补偿类型"""
    ROLLBACK = "rollback"     # 回滚操作
    CLEANUP = "cleanup"       # 清理资源
    NOTIFY = "notify"         # 通知告警
    RETRY_LATER = "retry_later"  # 延迟重试
    SKIP = "skip"             # 跳过继续


class RetryConfig:
    """重试配置"""

    def __init__(
        self,
        max_retries: int = 3,
        strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
        base_delay: int = 60,  # 基础延迟（秒）
        max_delay: int = 3600,  # 最大延迟（秒）
        multiplier: float = 2.0,  # 指数退避乘数
        jitter: bool = True,  # 是否添加随机抖动
        retryable_errors: Optional[List[str]] = None,  # 可重试的错误类型
        non_retryable_errors: Optional[List[str]] = None  # 不可重试的错误类型
    ):
        self.max_retries = max_retries
        self.strategy = strategy
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.multiplier = multiplier
        self.jitter = jitter
        self.retryable_errors = retryable_errors or []
        self.non_retryable_errors = non_retryable_errors or [
            "AuthenticationError",
            "PermissionDenied",
            "InvalidConfiguration"
        ]


class RetryService:
    """任务重试服务"""

    def __init__(self):
        self.default_config = RetryConfig()
        self.compensation_handlers: Dict[str, Callable] = {}
        self._retry_queue: asyncio.Queue = asyncio.Queue()
        self._running = False

    async def start(self):
        """启动重试服务"""
        self._running = True
        asyncio.create_task(self._process_retry_queue())
        logger.info("任务重试服务已启动")

    async def stop(self):
        """停止重试服务"""
        self._running = False
        logger.info("任务重试服务已停止")

    def calculate_delay(
        self,
        retry_count: int,
        config: Optional[RetryConfig] = None
    ) -> int:
        """
        计算重试延迟时间
        
        Args:
            retry_count: 当前重试次数
            config: 重试配置
            
        Returns:
            延迟时间（秒）
        """
        config = config or self.default_config

        if config.strategy == RetryStrategy.FIXED:
            delay = config.base_delay
        elif config.strategy == RetryStrategy.EXPONENTIAL:
            delay = config.base_delay * (config.multiplier ** retry_count)
        elif config.strategy == RetryStrategy.LINEAR:
            delay = config.base_delay * (retry_count + 1)
        else:
            delay = config.base_delay

        # 限制最大延迟
        delay = min(delay, config.max_delay)

        # 添加随机抖动（±10%）
        if config.jitter:
            import random
            jitter_range = delay * 0.1
            delay = delay + random.uniform(-jitter_range, jitter_range)

        return int(delay)

    def should_retry(
        self,
        error: str,
        retry_count: int,
        config: Optional[RetryConfig] = None
    ) -> bool:
        """
        判断是否应该重试
        
        Args:
            error: 错误信息
            retry_count: 当前重试次数
            config: 重试配置
            
        Returns:
            是否应该重试
        """
        config = config or self.default_config

        # 检查重试次数
        if retry_count >= config.max_retries:
            return False

        # 检查不可重试的错误
        for non_retryable in config.non_retryable_errors:
            if non_retryable.lower() in error.lower():
                return False

        # 如果指定了可重试错误列表，检查是否匹配
        if config.retryable_errors:
            for retryable in config.retryable_errors:
                if retryable.lower() in error.lower():
                    return True
            return False

        # 默认可重试
        return True

    async def schedule_retry(
        self,
        task: ScheduledTask,
        execution: TaskExecution,
        error: str,
        config: Optional[RetryConfig] = None
    ) -> Optional[datetime]:
        """
        调度任务重试
        
        Args:
            task: 任务对象
            execution: 执行记录
            error: 错误信息
            config: 重试配置
            
        Returns:
            下次重试时间，如果不重试则返回None
        """
        config = config or self._get_task_retry_config(task)

        current_retry = execution.retry_count

        if not self.should_retry(error, current_retry, config):
            logger.info(f"任务 {task.name} 不满足重试条件，执行补偿操作")
            await self._execute_compensation(task, execution, error)
            return None

        # 计算延迟
        delay = self.calculate_delay(current_retry, config)
        next_retry_time = datetime.now() + timedelta(seconds=delay)

        # 更新执行记录
        execution.retry_count = current_retry + 1
        execution.status = TaskStatus.PENDING
        execution.error_message = f"重试 {execution.retry_count}/{config.max_retries}: {error}"
        await execution.save()

        # 更新任务状态
        task.failure_count += 1
        await task.save()

        # 添加到重试队列
        await self._retry_queue.put({
            "task_id": task.id,
            "execution_id": execution.execution_id,
            "retry_time": next_retry_time,
            "retry_count": execution.retry_count
        })

        logger.info(
            f"任务 {task.name} 已调度重试 "
            f"({execution.retry_count}/{config.max_retries})，"
            f"延迟 {delay} 秒，下次执行时间: {next_retry_time}"
        )

        return next_retry_time

    async def manual_retry(
        self,
        execution_id: str,
        user_id: int
    ) -> Dict[str, Any]:
        """
        手动重试任务
        
        Args:
            execution_id: 执行记录ID
            user_id: 操作用户ID
            
        Returns:
            重试结果
        """
        execution = await TaskExecution.get_or_none(execution_id=execution_id)
        if not execution:
            return {"success": False, "error": "执行记录不存在"}

        task = await ScheduledTask.get_or_none(id=execution.task_id)
        if not task:
            return {"success": False, "error": "任务不存在"}

        # 检查执行状态
        if execution.status == TaskStatus.RUNNING:
            return {"success": False, "error": "任务正在执行中"}

        # 重置执行状态
        execution.status = TaskStatus.PENDING
        execution.retry_count += 1
        execution.error_message = f"手动重试 by user {user_id}"
        await execution.save()

        # 触发任务执行
        from src.services.scheduler import scheduler_service
        await scheduler_service.trigger_task(task.id)

        logger.info(f"任务 {task.name} 已手动触发重试 by user {user_id}")

        return {
            "success": True,
            "message": "任务已触发重试",
            "execution_id": execution_id,
            "retry_count": execution.retry_count
        }

    def register_compensation_handler(
        self,
        task_type: str,
        handler: Callable
    ):
        """
        注册补偿处理器
        
        Args:
            task_type: 任务类型
            handler: 补偿处理函数
        """
        self.compensation_handlers[task_type] = handler
        logger.info(f"已注册补偿处理器: {task_type}")

    async def _execute_compensation(
        self,
        task: ScheduledTask,
        execution: TaskExecution,
        error: str
    ):
        """
        执行补偿操作
        
        Args:
            task: 任务对象
            execution: 执行记录
            error: 错误信息
        """
        try:
            # 更新任务状态为失败
            task.status = TaskStatus.FAILED
            task.failure_count += 1
            await task.save()

            # 更新执行记录
            execution.status = TaskStatus.FAILED
            execution.end_time = datetime.now()
            execution.error_message = f"重试耗尽: {error}"
            await execution.save()

            # 执行自定义补偿处理器
            task_type = str(task.task_type.value) if task.task_type else "default"
            handler = self.compensation_handlers.get(task_type)

            if handler:
                await handler(task, execution, error)
                logger.info(f"任务 {task.name} 补偿处理完成")

            # 发送告警通知
            await self._send_failure_alert(task, execution, error)

        except Exception as e:
            logger.error(f"执行补偿操作失败: {e}")

    async def _send_failure_alert(
        self,
        task: ScheduledTask,
        execution: TaskExecution,
        error: str
    ):
        """发送任务失败告警"""
        try:
            from src.services.alert import alert_service

            alert_message = (
                f"任务执行失败告警\n"
                f"任务名称: {task.name}\n"
                f"执行ID: {execution.execution_id}\n"
                f"重试次数: {execution.retry_count}\n"
                f"错误信息: {error}\n"
                f"失败时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

            await alert_service.send_alert(
                title=f"任务失败: {task.name}",
                content=alert_message,
                level="error"
            )

        except Exception as e:
            logger.error(f"发送任务失败告警失败: {e}")

    async def _process_retry_queue(self):
        """处理重试队列"""
        while self._running:
            try:
                # 非阻塞获取，超时1秒
                try:
                    retry_item = await asyncio.wait_for(
                        self._retry_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                # 等待到重试时间
                retry_time = retry_item["retry_time"]
                now = datetime.now()

                if retry_time > now:
                    wait_seconds = (retry_time - now).total_seconds()
                    await asyncio.sleep(wait_seconds)

                # 执行重试
                task_id = retry_item["task_id"]
                from src.services.scheduler import scheduler_service
                await scheduler_service.trigger_task(task_id)

                logger.info(f"任务 {task_id} 重试已触发")

            except Exception as e:
                logger.error(f"处理重试队列失败: {e}")
                await asyncio.sleep(1)

    def _get_task_retry_config(self, task: ScheduledTask) -> RetryConfig:
        """获取任务的重试配置"""
        return RetryConfig(
            max_retries=task.retry_count,
            base_delay=task.retry_delay,
            strategy=RetryStrategy.EXPONENTIAL
        )

    async def get_retry_stats(self, task_id: int) -> Dict[str, Any]:
        """
        获取任务重试统计
        
        Args:
            task_id: 任务ID
            
        Returns:
            重试统计信息
        """
        executions = await TaskExecution.filter(task_id=task_id).all()

        total_executions = len(executions)
        retried_executions = sum(1 for e in executions if e.retry_count > 0)
        total_retries = sum(e.retry_count for e in executions)

        # 计算重试成功率
        retry_success = sum(
            1 for e in executions 
            if e.retry_count > 0 and e.status == TaskStatus.SUCCESS
        )
        retry_success_rate = (
            retry_success / retried_executions * 100 
            if retried_executions > 0 else 0
        )

        return {
            "task_id": task_id,
            "total_executions": total_executions,
            "retried_executions": retried_executions,
            "total_retries": total_retries,
            "retry_success_count": retry_success,
            "retry_success_rate": round(retry_success_rate, 2),
            "avg_retries_per_execution": (
                round(total_retries / retried_executions, 2)
                if retried_executions > 0 else 0
            )
        }

    async def get_pending_retries(self) -> List[Dict[str, Any]]:
        """获取待重试的任务列表"""
        pending = []

        # 从队列中获取待重试项（不移除）
        queue_size = self._retry_queue.qsize()
        items = []

        for _ in range(queue_size):
            try:
                item = self._retry_queue.get_nowait()
                items.append(item)
                pending.append({
                    "task_id": item["task_id"],
                    "execution_id": item["execution_id"],
                    "retry_time": item["retry_time"].isoformat(),
                    "retry_count": item["retry_count"]
                })
            except asyncio.QueueEmpty:
                break

        # 放回队列
        for item in items:
            await self._retry_queue.put(item)

        return pending


# 全局实例
retry_service = RetryService()
