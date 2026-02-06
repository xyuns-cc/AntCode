"""任务重试与补偿服务"""

import asyncio
from datetime import datetime, timedelta
from enum import Enum

from loguru import logger

from antcode_core.domain.models.enums import TaskStatus
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun


class RetryStrategy(str, Enum):
    """重试策略"""

    FIXED = "fixed"
    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    CUSTOM = "custom"


class CompensationType(str, Enum):
    """补偿类型"""

    ROLLBACK = "rollback"
    CLEANUP = "cleanup"
    NOTIFY = "notify"
    RETRY_LATER = "retry_later"
    SKIP = "skip"


class RetryConfig:
    """重试配置"""

    def __init__(
        self,
        max_retries=3,
        strategy=RetryStrategy.EXPONENTIAL,
        base_delay=60,
        max_delay=3600,
        multiplier=2.0,
        jitter=True,
        retryable_errors=None,
        non_retryable_errors=None,
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
            "InvalidConfiguration",
        ]


class RetryService:
    """任务重试服务"""

    def __init__(self):
        self.default_config = RetryConfig()
        self.compensation_handlers = {}
        self._retry_queue = asyncio.Queue()
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

    def calculate_delay(self, retry_count, config=None):
        """计算重试延迟时间"""
        config = config or self.default_config

        if config.strategy == RetryStrategy.FIXED:
            delay = config.base_delay
        elif config.strategy == RetryStrategy.EXPONENTIAL:
            delay = config.base_delay * (config.multiplier**retry_count)
        elif config.strategy == RetryStrategy.LINEAR:
            delay = config.base_delay * (retry_count + 1)
        else:
            delay = config.base_delay

        delay = min(delay, config.max_delay)

        if config.jitter:
            import random

            jitter_range = delay * 0.1
            delay = delay + random.uniform(-jitter_range, jitter_range)

        return int(delay)

    def should_retry(self, error, retry_count, config=None):
        """判断是否应该重试"""
        config = config or self.default_config

        if retry_count >= config.max_retries:
            return False

        for non_retryable in config.non_retryable_errors:
            if non_retryable.lower() in error.lower():
                return False

        if config.retryable_errors:
            return any(retryable.lower() in error.lower() for retryable in config.retryable_errors)

        return True

    async def schedule_retry(self, task, execution, error, config=None):
        """调度任务重试"""
        config = config or self._get_task_retry_config(task)
        current_retry = execution.retry_count

        if not self.should_retry(error, current_retry, config):
            logger.info(f"任务 {task.name} 不满足重试条件，执行补偿操作")
            await self._execute_compensation(task, execution, error)
            return None

        delay = self.calculate_delay(current_retry, config)
        next_retry_time = datetime.now() + timedelta(seconds=delay)

        execution.retry_count = current_retry + 1
        execution.status = TaskStatus.PENDING
        execution.error_message = f"重试 {execution.retry_count}/{config.max_retries}: {error}"
        await execution.save()

        task.failure_count += 1
        await task.save()

        await self._retry_queue.put(
            {
                "task_id": task.id,
                "execution_id": execution.execution_id,
                "retry_time": next_retry_time,
                "retry_count": execution.retry_count,
            }
        )

        logger.info(
            f"任务 {task.name} 已调度重试 "
            f"({execution.retry_count}/{config.max_retries})，"
            f"延迟 {delay} 秒，下次执行时间: {next_retry_time}"
        )

        return next_retry_time

    async def manual_retry(self, execution_id, user_id):
        """手动重试任务"""
        execution = await TaskRun.get_or_none(execution_id=execution_id)
        if not execution:
            return {"success": False, "error": "执行记录不存在"}

        task = await Task.get_or_none(id=execution.task_id)
        if not task:
            return {"success": False, "error": "任务不存在"}

        if execution.status == TaskStatus.RUNNING:
            return {"success": False, "error": "任务正在执行中"}

        execution.status = TaskStatus.PENDING
        execution.retry_count += 1
        execution.error_message = f"手动重试 by user {user_id}"
        await execution.save()

        from antcode_master.loops.scheduler_loop import scheduler_service

        await scheduler_service.trigger_task(task.id)

        logger.info(f"任务 {task.name} 已手动触发重试 by user {user_id}")

        return {
            "success": True,
            "message": "任务已触发重试",
            "execution_id": execution_id,
            "retry_count": execution.retry_count,
        }

    def register_compensation_handler(self, task_type, handler):
        """注册补偿处理器"""
        self.compensation_handlers[task_type] = handler
        logger.info(f"已注册补偿处理器: {task_type}")

    async def _execute_compensation(self, task, execution, error):
        """执行补偿操作"""
        try:
            task.status = TaskStatus.FAILED
            task.failure_count += 1
            await task.save()

            execution.status = TaskStatus.FAILED
            execution.end_time = datetime.now()
            execution.error_message = f"重试耗尽: {error}"
            await execution.save()

            task_type = str(task.task_type.value) if task.task_type else "default"
            handler = self.compensation_handlers.get(task_type)

            if handler:
                await handler(task, execution, error)
                logger.info(f"任务 {task.name} 补偿处理完成")

            await self._send_failure_alert(task, execution, error)

        except Exception as e:
            logger.error(f"执行补偿操作失败: {e}")

    async def _send_failure_alert(self, task, execution, error):
        """发送任务失败告警"""
        try:
            from antcode_core.application.services.alert import alert_service

            alert_message = (
                f"任务执行失败告警\n"
                f"任务名称: {task.name}\n"
                f"执行ID: {execution.execution_id}\n"
                f"重试次数: {execution.retry_count}\n"
                f"错误信息: {error}\n"
                f"失败时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

            await alert_service.send_alert(
                title=f"任务失败: {task.name}", content=alert_message, level="error"
            )

        except Exception as e:
            logger.error(f"发送任务失败告警失败: {e}")

    async def _process_retry_queue(self):
        """处理重试队列"""
        while self._running:
            try:
                try:
                    retry_item = await asyncio.wait_for(self._retry_queue.get(), timeout=1.0)
                except TimeoutError:
                    continue

                retry_time = retry_item["retry_time"]
                now = datetime.now()

                if retry_time > now:
                    wait_seconds = (retry_time - now).total_seconds()
                    await asyncio.sleep(wait_seconds)

                task_id = retry_item["task_id"]
                from antcode_master.loops.scheduler_loop import scheduler_service

                await scheduler_service.trigger_task(task_id)

                logger.info(f"任务 {task_id} 重试已触发")

            except Exception as e:
                logger.error(f"处理重试队列失败: {e}")
                await asyncio.sleep(1)

    def _get_task_retry_config(self, task):
        """获取任务的重试配置"""
        return RetryConfig(
            max_retries=task.retry_count,
            base_delay=task.retry_delay,
            strategy=RetryStrategy.EXPONENTIAL,
        )

    async def get_retry_stats(self, task_id):
        """获取任务重试统计"""
        executions = await TaskRun.filter(task_id=task_id).all()

        total_executions = len(executions)
        retried_executions = sum(1 for e in executions if e.retry_count > 0)
        total_retries = sum(e.retry_count for e in executions)

        retry_success = sum(
            1 for e in executions if e.retry_count > 0 and e.status == TaskStatus.SUCCESS
        )
        retry_success_rate = (
            retry_success / retried_executions * 100 if retried_executions > 0 else 0
        )

        return {
            "task_id": task_id,
            "total_executions": total_executions,
            "retried_executions": retried_executions,
            "total_retries": total_retries,
            "retry_success_count": retry_success,
            "retry_success_rate": round(retry_success_rate, 2),
            "avg_retries_per_execution": (
                round(total_retries / retried_executions, 2) if retried_executions > 0 else 0
            ),
        }

    async def get_pending_retries(self):
        """获取待重试的任务列表"""
        pending = []
        queue_size = self._retry_queue.qsize()
        items = []

        for _ in range(queue_size):
            try:
                item = self._retry_queue.get_nowait()
                items.append(item)
                pending.append(
                    {
                        "task_id": item["task_id"],
                        "execution_id": item["execution_id"],
                        "retry_time": item["retry_time"].isoformat(),
                        "retry_count": item["retry_count"],
                    }
                )
            except asyncio.QueueEmpty:
                break

        for item in items:
            await self._retry_queue.put(item)

        return pending


retry_service = RetryService()
