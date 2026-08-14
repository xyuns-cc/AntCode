"""
沙箱模块

提供可插拔的沙箱实现，支持 no-op 模式。

Requirements: 7.4
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.models import (
    ExecPlan,
    ExecResult,
    RuntimeHandle,
)
from antcode_worker.executor.base import (
    BaseExecutor,
    ExecutorConfig,
    LogSink,
    NoOpLogSink,
)
from antcode_worker.executor.concurrency import ExecutionAdmission
from antcode_worker.executor.process import ProcessExecutor
from antcode_worker.executor.sandbox_cancellation import (
    SandboxRunMarker,
    cancel_pending_process_task,
    cancelled_before_process_start,
)
from antcode_worker.executor.sandbox_config import SandboxConfig
from antcode_worker.executor.sandbox_factory import create_sandbox as create_sandbox
from antcode_worker.executor.sandbox_factory import create_sandbox_executor as create_sandbox_executor
from antcode_worker.executor.sandbox_plan import SandboxPlanRequest, create_sandboxed_plan
from antcode_worker.executor.sandbox_provider import BasicSandbox, NoOpSandbox, SandboxProvider


@dataclass(frozen=True)
class SandboxExecution:
    """Immutable state for one sandbox execution attempt."""

    run_id: str
    exec_plan: ExecPlan
    runtime_handle: RuntimeHandle
    log_sink: LogSink
    marker: SandboxRunMarker


class SandboxExecutor(BaseExecutor):
    """
    沙箱执行器

    在沙箱环境中执行任务，支持可插拔的沙箱实现。

    Requirements: 7.4
    """

    def __init__(
        self,
        config: ExecutorConfig | None = None,
        sandbox_config: SandboxConfig | None = None,
        sandbox_provider: SandboxProvider | None = None,
    ):
        """
        初始化沙箱执行器

        Args:
            config: 执行器配置
            sandbox_config: 沙箱配置
            sandbox_provider: 沙箱提供者（可选，默认使用 BasicSandbox）
        """
        super().__init__(config)

        self.sandbox_config = sandbox_config or SandboxConfig()

        # 选择沙箱提供者
        if sandbox_provider:
            self._sandbox = sandbox_provider
        elif self.sandbox_config.enabled:
            self._sandbox = BasicSandbox(self.sandbox_config)
        else:
            self._sandbox = NoOpSandbox()

        # 内部使用 ProcessExecutor 执行
        self._process_executor = ProcessExecutor(config)

    @property
    def sandbox(self) -> SandboxProvider:
        """获取沙箱提供者"""
        return self._sandbox

    async def start(self) -> None:
        """启动执行器"""
        await super().start()
        await self._process_executor.start()

    async def stop(self, grace_period: float = 10.0) -> None:
        """停止执行器"""
        await self._process_executor.stop(grace_period)
        await super().stop(grace_period)

    async def resize_concurrency(self, max_concurrent: int) -> None:
        """Keep the sandbox admission gate and process gate in sync."""
        await super().resize_concurrency(max_concurrent)
        await self._process_executor.resize_concurrency(max_concurrent)

    async def run(
        self,
        exec_plan: ExecPlan,
        runtime_handle: RuntimeHandle,
        log_sink: LogSink | None = None,
        *,
        admission: ExecutionAdmission | None = None,
    ) -> ExecResult:
        """
        在沙箱中执行任务

        Args:
            exec_plan: 执行计划
            runtime_handle: 运行时句柄
            log_sink: 日志接收器

        Returns:
            ExecResult 执行结果

        Requirements: 7.4
        """
        sink = log_sink or NoOpLogSink()
        # P0-02: 用真实 run_id 注册任务；plugin_name 是共享键（"rule"/"code"），
        # 会让 base.cancel() 找不到真 run_id 而直接返回 False，也会让同插件并发任务互相覆盖。
        run_id = exec_plan.run_id or exec_plan.plugin_name or f"sandbox_{id(exec_plan)}"

        marker = SandboxRunMarker()
        await self._register_task(run_id, marker)
        try:
            if admission is not None:
                await admission.executor_ready()
            if marker.cancel_requested:
                return cancelled_before_process_start(self, run_id, datetime.now())
            async with self._concurrency_gate.slot():
                return await self._execute_in_sandbox(
                    SandboxExecution(
                        run_id=run_id,
                        exec_plan=exec_plan,
                        runtime_handle=runtime_handle,
                        log_sink=sink,
                        marker=marker,
                    )
                )
        finally:
            await self._unregister_task(run_id)

    async def _execute_in_sandbox(
        self,
        execution: SandboxExecution,
    ) -> ExecResult:
        """在沙箱中执行任务"""
        started_at = datetime.now()
        context: dict[str, Any] = {}
        process_task: asyncio.Task[ExecResult] | None = None
        try:
            work_dir = execution.exec_plan.cwd or execution.runtime_handle.path
            context = await self._sandbox.prepare(execution.exec_plan, work_dir)
            sandboxed_plan = self._create_sandboxed_plan(
                execution.exec_plan,
                execution.runtime_handle,
                context,
            )
            if execution.marker.cancel_requested:
                result = cancelled_before_process_start(self, execution.run_id, started_at)
                self._update_stats(result.status)
                return result
            startup_event = asyncio.Event()
            process_task = asyncio.create_task(
                self._process_executor.run(
                    sandboxed_plan,
                    execution.runtime_handle,
                    execution.log_sink,
                    startup_event=startup_event,
                )
            )
            await startup_event.wait()
            if execution.marker.cancel_requested:
                await self._process_executor.cancel(execution.run_id)
            result = await process_task
            self._update_stats(result.status)
            return result
        except Exception as exc:
            return self._sandbox_failure(execution.run_id, started_at, exc)
        finally:
            await cancel_pending_process_task(process_task)
            if context:
                await self._sandbox.cleanup(context)

    def _sandbox_failure(self, run_id: str, started_at: datetime, error: Exception) -> ExecResult:
        logger.error("沙箱执行异常: {}, error={}", run_id, error)
        result = self._create_result(
            run_id=run_id,
            status=RunStatus.FAILED,
            exit_reason=ExitReason.ERROR,
            error_message=str(error),
            started_at=started_at,
            finished_at=datetime.now(),
        )
        self._update_stats(RunStatus.FAILED)
        return result

    def _create_sandboxed_plan(
        self,
        exec_plan: ExecPlan,
        runtime_handle: RuntimeHandle,
        context: dict[str, Any],
    ) -> ExecPlan:
        """创建沙箱化的执行计划"""
        return create_sandboxed_plan(
            self._sandbox,
            self.config,
            SandboxPlanRequest(
                exec_plan=exec_plan,
                runtime_handle=runtime_handle,
                context=context,
            ),
        )

    async def _do_cancel(self, run_id: str, task_info: Any) -> None:
        """执行取消操作"""
        if isinstance(task_info, SandboxRunMarker):
            task_info.cancel_requested = True
        await self._process_executor.cancel(run_id)
