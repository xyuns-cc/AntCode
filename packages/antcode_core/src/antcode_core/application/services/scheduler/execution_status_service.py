"""执行状态更新服务"""

from datetime import UTC, datetime

from loguru import logger

from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, TaskStatus
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun


class ExecutionStatusService:
    def __init__(self):
        self._dispatch_order = {
            DispatchStatus.PENDING: 0,
            DispatchStatus.DISPATCHING: 1,
            DispatchStatus.DISPATCHED: 2,
            DispatchStatus.ACKED: 3,
            DispatchStatus.REJECTED: 3,
            DispatchStatus.TIMEOUT: 3,
            DispatchStatus.FAILED: 3,
        }
        self._runtime_order = {
            RuntimeStatus.QUEUED: 1,
            RuntimeStatus.RUNNING: 2,
            RuntimeStatus.SUCCESS: 3,
            RuntimeStatus.FAILED: 3,
            RuntimeStatus.CANCELLED: 3,
            RuntimeStatus.TIMEOUT: 3,
            RuntimeStatus.SKIPPED: 3,
        }
        self._runtime_terminal = {
            RuntimeStatus.SUCCESS,
            RuntimeStatus.FAILED,
            RuntimeStatus.CANCELLED,
            RuntimeStatus.TIMEOUT,
            RuntimeStatus.SKIPPED,
        }
        self._dispatch_terminal = {
            DispatchStatus.REJECTED,
            DispatchStatus.TIMEOUT,
            DispatchStatus.FAILED,
        }

    def _ensure_dt(self, status_at):
        if status_at is None:
            return datetime.now(UTC)
        if status_at.tzinfo is None:
            return status_at.replace(tzinfo=UTC)
        return status_at

    def _normalize_dispatch(self, status):
        if isinstance(status, DispatchStatus):
            return status
        if isinstance(status, str):
            try:
                return DispatchStatus(status)
            except ValueError:
                return None
        return None

    def _normalize_runtime(self, status):
        if isinstance(status, RuntimeStatus):
            return status
        if isinstance(status, str):
            try:
                return RuntimeStatus(status)
            except ValueError:
                return None
        return None

    def _derive_overall(self, dispatch_status, runtime_status):
        if runtime_status:
            runtime_map = {
                RuntimeStatus.QUEUED: TaskStatus.QUEUED,
                RuntimeStatus.RUNNING: TaskStatus.RUNNING,
                RuntimeStatus.SUCCESS: TaskStatus.SUCCESS,
                RuntimeStatus.FAILED: TaskStatus.FAILED,
                RuntimeStatus.CANCELLED: TaskStatus.CANCELLED,
                RuntimeStatus.TIMEOUT: TaskStatus.TIMEOUT,
                RuntimeStatus.SKIPPED: TaskStatus.SKIPPED,
            }
            return runtime_map.get(runtime_status, TaskStatus.PENDING)

        dispatch_map = {
            DispatchStatus.PENDING: TaskStatus.PENDING,
            DispatchStatus.DISPATCHING: TaskStatus.DISPATCHING,
            DispatchStatus.DISPATCHED: TaskStatus.QUEUED,
            DispatchStatus.ACKED: TaskStatus.QUEUED,
            DispatchStatus.REJECTED: TaskStatus.REJECTED,
            DispatchStatus.TIMEOUT: TaskStatus.TIMEOUT,
            DispatchStatus.FAILED: TaskStatus.FAILED,
        }
        return dispatch_map.get(dispatch_status, TaskStatus.PENDING)

    def _should_update(self, current_status, current_at, new_status, new_at, order_map):
        current_order = order_map.get(current_status, 0) if current_status else 0
        new_order = order_map.get(new_status, 0)
        if new_order < current_order:
            return False
        return not (new_order == current_order and current_at and new_at <= current_at)

    async def update_dispatch_status(
        self,
        run_id,
        status,
        status_at=None,
        worker_id=None,
        error_message=None,
    ):
        new_status = self._normalize_dispatch(status)
        if not new_status:
            logger.warning(f"无效分发状态: {status}")
            return False

        status_at = self._ensure_dt(status_at)
        execution = await TaskRun.get_or_none(run_id=run_id)
        if not execution:
            logger.warning(f"执行记录不存在: {run_id}")
            return False

        if not self._should_update(
            execution.dispatch_status,
            execution.dispatch_updated_at,
            new_status,
            status_at,
            self._dispatch_order,
        ):
            return False

        execution.dispatch_status = new_status
        execution.dispatch_updated_at = status_at
        if worker_id:
            execution.worker_id = worker_id
        if error_message:
            execution.error_message = error_message

        if new_status in self._dispatch_terminal and not execution.runtime_status and not execution.end_time:
            execution.end_time = status_at
        execution.status = self._derive_overall(execution.dispatch_status, execution.runtime_status)

        await execution.save()
        await self._sync_task_status(execution, status_at)
        return True

    async def update_runtime_status(
        self,
        run_id,
        status,
        status_at=None,
        exit_code=None,
        error_message=None,
    ):
        new_status = self._normalize_runtime(status)
        if not new_status:
            logger.warning(f"无效运行状态: {status}")
            return False

        status_at = self._ensure_dt(status_at)
        execution = await TaskRun.get_or_none(run_id=run_id)
        if not execution:
            logger.warning(f"执行记录不存在: {run_id}")
            return False

        if not self._should_update(
            execution.runtime_status,
            execution.runtime_updated_at,
            new_status,
            status_at,
            self._runtime_order,
        ):
            return False

        execution.runtime_status = new_status
        execution.runtime_updated_at = status_at
        execution.last_heartbeat = status_at
        if exit_code is not None:
            execution.exit_code = exit_code
        if error_message:
            execution.error_message = error_message

        if new_status == RuntimeStatus.RUNNING and not execution.start_time:
            execution.start_time = status_at
        if new_status in self._runtime_terminal:
            if not execution.end_time:
                execution.end_time = status_at
            if execution.start_time and not execution.duration_seconds:
                execution.duration_seconds = (execution.end_time - execution.start_time).total_seconds()

        execution.status = self._derive_overall(execution.dispatch_status, execution.runtime_status)

        await execution.save()
        await self._sync_task_status(execution, status_at)
        return True

    async def _sync_task_status(self, execution, status_at):
        task = await Task.get_or_none(id=execution.task_id)
        if not task:
            return

        previous_status = task.status
        task.status = execution.status
        if execution.runtime_status == RuntimeStatus.RUNNING:
            task.last_run_time = status_at

        if task.status == TaskStatus.SUCCESS and previous_status != TaskStatus.SUCCESS:
            task.success_count = (task.success_count or 0) + 1
        elif task.status in (TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.REJECTED):
            if previous_status not in (TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.REJECTED):
                task.failure_count = (task.failure_count or 0) + 1

        await task.save()


execution_status_service = ExecutionStatusService()
