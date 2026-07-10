"""执行状态更新服务"""

from datetime import UTC, datetime

from loguru import logger
from tortoise.expressions import F, Q

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
        # P1-12: ACKED 也是 dispatch 终态——节点已确认，不允许后到的 REJECTED/TIMEOUT
        # 翻转已经 ACKED 的记录（否则会出现 "已确认→被拒绝" 的伪矛盾状态）。
        self._dispatch_terminal = {
            DispatchStatus.ACKED,
            DispatchStatus.REJECTED,
            DispatchStatus.TIMEOUT,
            DispatchStatus.FAILED,
        }
        self._failure_task_states = (
            TaskStatus.FAILED,
            TaskStatus.TIMEOUT,
            TaskStatus.REJECTED,
        )

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

    def _should_update(
        self,
        current_status,
        current_at,
        new_status,
        new_at,
        order_map,
        terminal_set=None,
    ):
        """B5: 终态吸收态——一旦落到 terminal_set，任何后续 runtime 状态都拒绝写入。

        旧行为：同 rank + 更新时间戳递增即允许覆盖 → reconcile 的 TIMEOUT 可被
        迟到的 worker SUCCESS 覆盖；user_cancel 可被后到的 SUCCESS 复活。
        新行为：current 已是终态 → 直接返回 False；避免复活/覆盖。
        """
        if terminal_set is not None and current_status in terminal_set:
            return False
        current_order = order_map.get(current_status, 0) if current_status else 0
        new_order = order_map.get(new_status, 0)
        if new_order < current_order:
            return False
        return not (new_order == current_order and current_at and new_at <= current_at)

    def _allowed_from_states(self, new_status, order_map):
        """P1-12 CAS 语义：只允许从 rank 严格更低的状态转入 new_status。

        终态 rank 相同（例如 dispatch 侧的 REJECTED/TIMEOUT/FAILED 都是 3），
        因此不会出现 "终态 → 另一终态" 的覆盖，与 ``_should_update`` 的
        terminal_set 语义保持一致。
        """
        new_rank = order_map.get(new_status, 0)
        return [s for s, r in order_map.items() if r < new_rank]

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
            terminal_set=self._dispatch_terminal,
        ):
            return False

        # 组装 UPDATE 字段（不走 fetch → 修改 → save 的 read-check-save）
        updates = {
            "dispatch_status": new_status,
            "dispatch_updated_at": status_at,
        }
        if worker_id:
            updates["worker_id"] = worker_id
        if error_message:
            updates["error_message"] = error_message

        if (
            new_status in self._dispatch_terminal
            and not execution.runtime_status
            and not execution.end_time
        ):
            updates["end_time"] = status_at

        updates["status"] = self._derive_overall(new_status, execution.runtime_status)

        # CAS：只有当 DB 里当前 dispatch_status 仍然在允许来源集合中才写入。
        # 允许来源 = rank 严格小于 new_status 的状态，避免终态被后到消息覆盖 /
        # 并发多写互相踩踏。
        allowed_from = self._allowed_from_states(new_status, self._dispatch_order)
        cas_q = Q(dispatch_status__isnull=True)
        if allowed_from:
            cas_q = cas_q | Q(dispatch_status__in=allowed_from)

        updated = await TaskRun.filter(run_id=run_id).filter(cas_q).update(**updates)
        if not updated:
            logger.debug(
                f"分发状态 CAS 失败(并发写入或终态保护): run_id={run_id} -> {new_status}"
            )
            return False

        # 重新加载最新行给 _sync_task_status 使用，避免用陈旧的 execution 视图。
        refreshed = await TaskRun.get_or_none(run_id=run_id)
        if refreshed is not None:
            await self._sync_task_status(refreshed, status_at)
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
            terminal_set=self._runtime_terminal,
        ):
            return False

        # 组装 UPDATE 字段（真 CAS：filter().update() 而不是 fetch → 修改 → save）
        updates = {
            "runtime_status": new_status,
            "runtime_updated_at": status_at,
            "last_heartbeat": status_at,
        }
        if exit_code is not None:
            updates["exit_code"] = exit_code
        if error_message:
            updates["error_message"] = error_message

        start_time = execution.start_time
        if new_status == RuntimeStatus.RUNNING and not execution.start_time:
            updates["start_time"] = status_at
            start_time = status_at

        end_time = execution.end_time
        if new_status in self._runtime_terminal:
            if not execution.end_time:
                updates["end_time"] = status_at
                end_time = status_at
            if start_time and not execution.duration_seconds and end_time:
                updates["duration_seconds"] = (end_time - start_time).total_seconds()

        updates["status"] = self._derive_overall(execution.dispatch_status, new_status)

        # CAS：允许来源 = rank 严格小于 new_status 的状态（含 NULL 的初始态）。
        # 并发的两条终态回报里，只有一条能命中（另一条命中时 runtime_status 已被
        # 更新到终态，rank 等于 new_rank，不在允许集合中，UPDATE 返回 0）。
        allowed_from = self._allowed_from_states(new_status, self._runtime_order)
        cas_q = Q(runtime_status__isnull=True)
        if allowed_from:
            cas_q = cas_q | Q(runtime_status__in=allowed_from)

        updated = await TaskRun.filter(run_id=run_id).filter(cas_q).update(**updates)
        if not updated:
            logger.debug(
                f"运行状态 CAS 失败(并发写入或终态保护): run_id={run_id} -> {new_status}"
            )
            return False

        refreshed = await TaskRun.get_or_none(run_id=run_id)
        if refreshed is not None:
            await self._sync_task_status(refreshed, status_at)
        return True

    async def _sync_task_status(self, execution, status_at):
        # P1-12: latest-run 约束。旧 run 迟到的终态回报不能覆盖新 run 的 Task 状态
        # （例如 run_A 已被取消/超时后 reconcile 又跑了 run_B，此时旧的 run_A worker
        # 汇报 SUCCESS 不应把 Task 翻回 SUCCESS）。
        latest = await TaskRun.filter(task_id=execution.task_id).order_by("-id").first()
        if latest is None or latest.id != execution.id:
            logger.debug(
                f"跳过 Task 状态同步(非最新 run): task_id={execution.task_id} "
                f"execution.id={execution.id} latest.id={getattr(latest, 'id', None)}"
            )
            return

        task = await Task.get_or_none(id=execution.task_id)
        if not task:
            return

        previous_status = task.status
        new_task_status = execution.status

        # 组装 UPDATE 字段——计数器改用 F() 原子自增，Task 状态走一次 filter().update()，
        # 不再 fetch → 修改 → save（并发时的丢计数 / Task 状态互相覆盖问题就此解决）。
        updates: dict = {"status": new_task_status}
        if execution.runtime_status == RuntimeStatus.RUNNING:
            updates["last_run_time"] = status_at

        if new_task_status == TaskStatus.SUCCESS and previous_status != TaskStatus.SUCCESS:
            updates["success_count"] = F("success_count") + 1
        elif (
            new_task_status in self._failure_task_states
            and previous_status not in self._failure_task_states
        ):
            updates["failure_count"] = F("failure_count") + 1

        await Task.filter(id=task.id).update(**updates)


execution_status_service = ExecutionStatusService()
