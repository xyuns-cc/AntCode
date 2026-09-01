"""重试查询/手动重试服务(web_api 侧)

重试队列的**消费者**只有 Master 的 ``antcode_master.control.retry_loop``:
它在 Leader 门禁下 claim ``{ns}:retry:pending``,并按 durable intent 的血缘
创建新 run。本模块只做请求作用域内的读与取消,不消费队列。
"""

from __future__ import annotations

from antcode_core.application.services.scheduler.manual_retry_outbox import get_manual_retry_event
from antcode_core.application.services.scheduler.manual_retry_service import execute_manual_retry
from antcode_core.application.services.scheduler.outbox_service import scheduler_outbox_service
from antcode_core.application.services.scheduler.retry_queue import RetryQueueBackend
from antcode_core.application.services.scheduler.retry_statistics import build_retry_stats
from antcode_core.domain.models.task_run import TaskRun


class RetryService:
    """任务重试服务"""

    def __init__(self):
        self._backend = RetryQueueBackend()

    async def manual_retry(self, run_id, user_id):
        """手动重试通过事务服务创建新 run，历史 run 保持不可变。"""
        return await execute_manual_retry(
            run_id,
            user_id,
            cancel_pending=self._backend.cancel,
            enqueue_event=scheduler_outbox_service.enqueue,
            get_event=get_manual_retry_event,
        )

    async def cancel_pending(self, run_id: str) -> int:
        """把待重试意图从 Redis pending 队列移除（配合 DB 清 next_retry_at）。"""
        return await self._backend.cancel(run_id)

    async def get_retry_stats(self, task_id):
        """获取任务重试统计"""
        executions = await TaskRun.filter(task_id=task_id).all()
        return build_retry_stats(task_id, executions)

    async def get_pending_retries(self):
        """从 PostgreSQL 权威 durable intent 获取待重试列表。"""
        from antcode_core.application.services.scheduler.retry_pending_query import (
            list_durable_pending_retries,
        )

        return await list_durable_pending_retries()


retry_service = RetryService()
