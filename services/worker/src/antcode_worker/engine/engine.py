"""
引擎核心

实现任务生命周期管理：poll -> schedule -> execute -> report

Requirements: 4.1, 4.5, 4.6, 4.7, 4.8
"""

import asyncio
import contextlib
from datetime import datetime
from typing import Any

from loguru import logger

from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.models import ExecResult, RunContext
from antcode_worker.engine.policies import Policies, default_policies
from antcode_worker.engine.scheduler import Scheduler
from antcode_worker.engine.state import RunState, StateManager


class Engine:
    """
    引擎核心

    主循环：poll -> schedule -> execute -> report

    Requirements: 4.1, 4.5, 4.6, 4.7, 4.8
    """

    def __init__(
        self,
        transport: Any,
        executor: Any,
        flow_controller: Any = None,
        runtime_manager: Any = None,
        plugin_registry: Any = None,
        log_manager_factory: Any = None,
        project_fetcher: Any = None,
        artifact_manager: Any = None,
        policies: Policies | None = None,
        max_concurrent: int = 5,
        memory_limit_mb: int = 0,
        cpu_limit_seconds: int = 0,
    ):
        self._transport = transport
        self._executor = executor
        self._flow_controller = flow_controller
        self._runtime_manager = runtime_manager
        self._plugin_registry = plugin_registry
        self._log_manager_factory = log_manager_factory
        self._project_fetcher = project_fetcher
        self._artifact_manager = artifact_manager
        self._policies = policies or default_policies()
        self._max_concurrent = max_concurrent

        self._scheduler = Scheduler(max_queue_size=max_concurrent * 2)
        self._state_manager = StateManager()

        self._running = False
        self._polling = False
        self._poll_task: asyncio.Task | None = None
        self._control_task: asyncio.Task | None = None
        self._worker_tasks: list[asyncio.Task] = []
        self._runtime_control_semaphore = asyncio.Semaphore(1)

        # 资源限制
        self._policies.resource.max_concurrent = max_concurrent
        self._policies.resource.memory_limit_mb = memory_limit_mb
        self._policies.resource.cpu_limit_seconds = cpu_limit_seconds

    @property
    def scheduler(self) -> Scheduler:
        return self._scheduler

    @property
    def state_manager(self) -> StateManager:
        return self._state_manager

    async def start(self) -> None:
        """启动引擎"""
        if self._running:
            return

        self._running = True
        self._polling = True

        # 启动调度器
        await self._scheduler.start()

        # 启动轮询任务
        self._poll_task = asyncio.create_task(self._poll_loop())

        # 启动控制通道轮询
        self._control_task = asyncio.create_task(self._control_loop())

        # 启动工作协程
        for i in range(self._max_concurrent):
            task = asyncio.create_task(self._worker_loop(i))
            self._worker_tasks.append(task)

        logger.info(f"引擎已启动 (workers={self._max_concurrent})")

    async def stop(self, grace_period: float = 30.0) -> None:
        """
        停止引擎

        1. 停止接收新任务
        2. 等待运行中任务完成
        3. 强制终止未完成任务
        """
        if not self._running:
            return

        logger.info("开始停止引擎...")

        # 停止轮询
        self._polling = False

        # 取消轮询任务
        if self._poll_task:
            self._poll_task.cancel()
        if self._control_task:
            self._control_task.cancel()

        # 等待运行中任务完成
        active_count = await self._state_manager.count_active()
        if active_count > 0:
            logger.info(f"等待 {active_count} 个任务完成 (最长 {grace_period}s)...")
            try:
                await asyncio.wait_for(
                    self._drain_tasks(),
                    timeout=grace_period,
                )
            except TimeoutError:
                logger.warning("等待超时，强制终止任务")
                await self._force_terminate()

        # 停止工作协程
        self._running = False
        for task in self._worker_tasks:
            task.cancel()

        # 停止调度器
        await self._scheduler.stop()

        logger.info("引擎已停止")

    async def _poll_loop(self) -> None:
        """任务轮询循环"""
        while self._polling:
            flow_acquired = False
            try:
                if not self._transport or not self._transport.is_connected:
                    await asyncio.sleep(0.5)
                    continue

                # 检查是否有空间
                if self._scheduler.is_full:
                    await asyncio.sleep(1)
                    continue

                if self._flow_controller:
                    flow_acquired = await self._flow_controller.acquire(
                        timeout=self._policies.timeout.poll_timeout
                    )
                    if not flow_acquired:
                        await asyncio.sleep(0.1)
                        continue

                # 拉取任务
                task_msg = await self._transport.poll_task(
                    timeout=self._policies.timeout.poll_timeout
                )
                if self._flow_controller:
                    self._flow_controller.on_success()

                if task_msg is None:
                    continue

                # 创建运行上下文
                runtime_env_name = None
                environment = getattr(task_msg, "environment", {}) or {}
                if isinstance(environment, dict):
                    runtime_env_name = environment.get("ANTCODE_RUNTIME_ENV")
                labels = {}
                if runtime_env_name:
                    labels["runtime_env_name"] = runtime_env_name
                run_id = getattr(task_msg, "run_id", None) or self._generate_run_id(task_msg.task_id)
                context = RunContext(
                    run_id=run_id,
                    task_id=task_msg.task_id,
                    project_id=task_msg.project_id,
                    timeout_seconds=task_msg.timeout,
                    memory_limit_mb=self._policies.resource.memory_limit_mb,
                    cpu_limit_seconds=self._policies.resource.cpu_limit_seconds,
                    priority=task_msg.priority,
                    labels=labels,
                    receipt=getattr(task_msg, "receipt", None),
                )

                # 添加到状态管理
                await self._state_manager.add(run_id, task_msg.task_id, receipt=task_msg.receipt)

                # 入队
                await self._scheduler.enqueue(
                    run_id=run_id,
                    data=(context, task_msg),
                    priority=task_msg.priority,
                )

                logger.info(f"任务入队: {run_id}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._flow_controller:
                    self._flow_controller.on_failure()
                logger.error(f"轮询异常: {e}")
                await asyncio.sleep(1)
            finally:
                if self._flow_controller and flow_acquired:
                    await self._flow_controller.release()

    async def _control_loop(self) -> None:
        """控制通道轮询（取消/kill）"""
        while self._running:
            try:
                if not self._transport or not self._transport.is_connected:
                    await asyncio.sleep(0.5)
                    continue

                control = await self._transport.poll_control(
                    timeout=self._policies.timeout.poll_timeout
                )
                if control is None:
                    continue

                if control.control_type in ("cancel", "kill"):
                    target = control.run_id or control.task_id
                    if target:
                        await self.cancel(target, reason=control.reason or control.control_type)
                elif control.control_type == "config_update":
                    await self.apply_config_update(control.payload or {})
                elif control.control_type == "runtime_manage":
                    asyncio.create_task(self._handle_runtime_control(control))
                    continue

                if control.receipt:
                    await self._transport.ack_control(control.receipt)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"控制通道异常: {e}")
                await asyncio.sleep(1)

    async def _handle_runtime_control(self, control: Any) -> None:
        """处理运行时管理控制消息"""
        payload = control.payload or {}
        action = payload.get("action", "")
        request_id = payload.get("request_id", "")
        reply_stream = payload.get("reply_stream", "")
        data = payload.get("payload") or {}

        from antcode_worker.runtime.uv_manager import uv_manager

        success = True
        result_data: Any = None
        error_message = ""

        try:
            async with self._runtime_control_semaphore:
                if action == "list_envs":
                    scope = data.get("scope") or None
                    result_data = await uv_manager.list_envs(scope=scope)
                elif action == "get_env":
                    env_name = data.get("env_name")
                    if not env_name:
                        raise RuntimeError("env_name 不能为空")
                    result_data = await uv_manager.get_env(env_name)
                    if result_data is None:
                        raise RuntimeError("环境不存在")
                elif action == "update_env":
                    env_name = data.get("env_name")
                    if not env_name:
                        raise RuntimeError("env_name 不能为空")
                    result_data = await uv_manager.update_env(
                        env_name=env_name,
                        key=data.get("key"),
                        description=data.get("description"),
                    )
                elif action == "create_env":
                    env_name = data.get("env_name")
                    python_version = data.get("python_version")
                    packages = data.get("packages") or []
                    created_by = data.get("created_by") or None
                    if not env_name:
                        raise RuntimeError("env_name 不能为空")
                    result_data = await uv_manager.create_env(
                        env_name=env_name,
                        python_version=python_version,
                        packages=packages,
                        created_by=created_by,
                    )
                elif action == "delete_env":
                    env_name = data.get("env_name")
                    if not env_name:
                        raise RuntimeError("env_name 不能为空")
                    deleted = await uv_manager.delete_env(env_name)
                    result_data = {"deleted": bool(deleted)}
                elif action == "list_packages":
                    env_name = data.get("env_name")
                    if not env_name:
                        raise RuntimeError("env_name 不能为空")
                    result_data = await uv_manager.list_packages(env_name)
                elif action == "install_packages":
                    env_name = data.get("env_name")
                    packages = data.get("packages") or []
                    upgrade = bool(data.get("upgrade", False))
                    if not env_name or not packages:
                        raise RuntimeError("env_name 和 packages 不能为空")
                    result_data = await uv_manager.install_packages(
                        env_name=env_name,
                        packages=packages,
                        upgrade=upgrade,
                    )
                elif action == "uninstall_packages":
                    env_name = data.get("env_name")
                    packages = data.get("packages") or []
                    if not env_name or not packages:
                        raise RuntimeError("env_name 和 packages 不能为空")
                    result_data = await uv_manager.uninstall_packages(
                        env_name=env_name,
                        packages=packages,
                    )
                elif action == "list_interpreters":
                    result_data = await uv_manager.list_all_interpreters()
                elif action == "install_interpreter":
                    version = data.get("version")
                    if not version:
                        raise RuntimeError("version 不能为空")
                    result_data = await uv_manager.install_interpreter(version)
                elif action == "uninstall_interpreter":
                    version = data.get("version")
                    if not version:
                        raise RuntimeError("version 不能为空")
                    result_data = await uv_manager.uninstall_interpreter(version)
                elif action == "register_interpreter":
                    python_bin = data.get("python_bin")
                    version = data.get("version") or None
                    if not python_bin:
                        raise RuntimeError("python_bin 不能为空")
                    result_data = await uv_manager.register_interpreter(
                        python_bin=python_bin,
                        version=version,
                    )
                elif action == "unregister_interpreter":
                    python_bin = data.get("python_bin") or None
                    version = data.get("version") or None
                    result_data = await uv_manager.unregister_interpreter(
                        python_bin=python_bin,
                        version=version,
                    )
                elif action == "get_python_versions":
                    installed = await uv_manager.get_installed_python_versions()
                    all_interpreters = await uv_manager.list_all_interpreters()
                    available = sorted(
                        {
                            interp.get("version")
                            for interp in installed
                            if interp.get("version")
                        }
                    )
                    platform_info = await uv_manager.get_platform_info_async()
                    result_data = {
                        "installed": installed,
                        "available": available,
                        "all_interpreters": all_interpreters,
                        "platform": platform_info,
                    }
                elif action == "get_platform_info":
                    result_data = await uv_manager.get_platform_info_async()
                else:
                    raise RuntimeError(f"未知运行时操作: {action}")
        except Exception as e:
            success = False
            error_message = str(e)
        finally:
            if request_id and reply_stream:
                await self._transport.send_control_result(
                    request_id=request_id,
                    reply_stream=reply_stream,
                    success=success,
                    data=result_data if success else None,
                    error=error_message,
                )
            if control.receipt:
                await self._transport.ack_control(control.receipt)

    async def _worker_loop(self, worker_id: int) -> None:
        """工作协程"""
        logger.debug(f"Worker-{worker_id} 启动")

        while self._running:
            try:
                # 从队列取任务
                item = await self._scheduler.dequeue(timeout=1.0)
                if item is None:
                    continue

                run_id, (context, task_msg) = item

                # 执行任务
                result = await self._execute_task(context, task_msg)

                # 上报结果
                await self._report_result(context, result)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker-{worker_id} 异常: {e}")

    async def _execute_task(self, context: RunContext, task_msg: Any) -> ExecResult:
        """执行单个任务"""
        run_id = context.run_id
        started_at = datetime.now()
        log_manager = None
        runtime_handle = None

        try:
            # 转换状态
            await self._state_manager.transition(run_id, RunState.PREPARING)

            # 生成任务 payload
            payload = self._build_payload(task_msg)

            # 下载/缓存项目
            if self._project_fetcher and payload.download_url:
                payload.project_path = await self._project_fetcher.fetch(
                    project_id=context.project_id,
                    download_url=payload.download_url,
                    file_hash=payload.file_hash,
                    is_compressed=payload.is_compressed,
                    entry_point=payload.entry_point,
                )

            # 准备运行时环境
            runtime_handle = await self._prepare_runtime(context)

            if await self._is_cancel_requested(run_id):
                return self._build_cancelled_result(run_id, started_at, "任务已取消")

            # 通过插件生成执行计划
            if self._plugin_registry:
                exec_plan = await self._plugin_registry.build_plan(context, payload)
            else:
                exec_plan = self._build_fallback_plan(context, payload, runtime_handle)

            exec_plan.run_id = run_id

            # 注入运行时环境变量
            if context.runtime_spec and context.runtime_spec.env_vars:
                exec_plan.env.update(context.runtime_spec.env_vars)

            if await self._is_cancel_requested(run_id):
                return self._build_cancelled_result(run_id, started_at, "任务已取消")

            # 转换状态
            await self._state_manager.transition(run_id, RunState.RUNNING)

            # 准备日志管理器
            log_sink = None
            if self._log_manager_factory:
                log_manager = self._log_manager_factory.create(run_id)
                await log_manager.start()
                log_sink = log_manager

            # 执行
            exec_result = await self._executor.run(
                exec_plan,
                runtime_handle,
                log_sink=log_sink,
            )

            # 收集产物
            if self._artifact_manager and exec_plan.artifact_patterns:
                collection = await self._artifact_manager.collect_artifacts(
                    work_dir=exec_plan.cwd or runtime_handle.path,
                    patterns=exec_plan.artifact_patterns,
                    run_id=run_id,
                )
                for artifact in collection.artifacts:
                    stored = await self._artifact_manager.store_artifact(artifact, run_id)
                    exec_result.artifacts.append(stored)

            # 归档日志
            if log_manager:
                archived = await log_manager.archive_logs()
                if archived:
                    exec_result.artifacts.extend(archived)
                    exec_result.log_archived = True
                    exec_result.log_archive_uri = archived[0].uri

            # 转换状态
            if exec_result.status == RunStatus.SUCCESS:
                await self._state_manager.transition(run_id, RunState.COMPLETED)
            elif exec_result.status == RunStatus.CANCELLED:
                info = await self._state_manager.get(run_id)
                if info and info.state != RunState.CANCELLED:
                    await self._state_manager.transition(run_id, RunState.CANCELLED)
            else:
                await self._state_manager.transition(run_id, RunState.FAILED)

            return exec_result

        except Exception as e:
            logger.error(f"执行失败: {run_id}, error={e}")
            await self._state_manager.transition(run_id, RunState.FAILED)
            return ExecResult(
                run_id=run_id,
                status=RunStatus.FAILED,
                exit_reason=ExitReason.ERROR,
                error_message=str(e),
                started_at=started_at,
                finished_at=datetime.now(),
            )
        finally:
            if log_manager:
                await log_manager.stop()
            if runtime_handle and self._runtime_manager:
                await self._runtime_manager.release(runtime_handle)

    async def _report_result(self, context: RunContext, result: ExecResult) -> None:
        """上报结果（幂等）"""
        from antcode_worker.transport.base import TaskResult

        task_result = TaskResult(
            run_id=context.run_id,
            task_id=context.task_id,
            status=result.status.value,
            exit_code=result.exit_code or 0,
            error_message=result.error_message or "",
            started_at=result.started_at,
            finished_at=result.finished_at,
            duration_ms=result.duration_ms,
            data={
                "artifacts": [a.to_dict() for a in result.artifacts],
                "log_archive_uri": result.log_archive_uri or "",
                "stdout_lines": result.stdout_lines,
                "stderr_lines": result.stderr_lines,
            },
        )

        # 幂等上报
        success = await self._transport.report_result(task_result)
        if success:
            logger.info(f"结果已上报: {context.run_id}")
        else:
            logger.warning(f"结果上报失败: {context.run_id}")

        # ACK 任务
        if context.receipt:
            await self._transport.ack_task(context.receipt, accepted=True)

        # 清理状态
        await self._state_manager.remove(context.run_id)

    async def _report_result_by_info(
        self,
        run_id: str,
        task_id: str,
        receipt: str | None,
        result: ExecResult,
    ) -> None:
        """使用最小信息上报结果"""
        context = RunContext(
            run_id=run_id,
            task_id=task_id,
            project_id="",
            receipt=receipt,
        )
        await self._report_result(context, result)

    def _build_cancelled_result(
        self,
        run_id: str,
        started_at: datetime,
        reason: str,
    ) -> ExecResult:
        """构建取消结果"""
        return ExecResult(
            run_id=run_id,
            status=RunStatus.CANCELLED,
            exit_reason=ExitReason.CANCELLED,
            error_message=reason,
            started_at=started_at,
            finished_at=datetime.now(),
        )

    async def _is_cancel_requested(self, run_id: str) -> bool:
        """判断是否已请求取消"""
        info = await self._state_manager.get(run_id)
        if not info:
            return False
        return bool(info.data.get("cancel_requested"))

    async def cancel(self, run_id: str, reason: str = "") -> bool:
        """取消任务"""
        info = await self._state_manager.get(run_id)
        if not info:
            return False

        if info.state in (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED):
            return False

        # 如果在队列中，直接移除
        if info.state == RunState.QUEUED:
            await self._scheduler.remove(run_id)
            await self._state_manager.transition(run_id, RunState.CANCELLED)
            await self._report_result_by_info(
                run_id=info.run_id,
                task_id=info.task_id,
                receipt=info.receipt,
                result=self._build_cancelled_result(info.run_id, info.queued_at or datetime.now(), reason),
            )
            logger.info(f"任务已取消: {run_id}, reason={reason}")
            return True

        info.data["cancel_requested"] = True

        if info.state == RunState.RUNNING:
            await self._state_manager.transition(run_id, RunState.CANCELLING)
            if self._executor:
                await self._executor.cancel(run_id)
        elif info.state == RunState.PREPARING:
            await self._state_manager.transition(run_id, RunState.CANCELLED)

        logger.info(f"任务已取消: {run_id}, reason={reason}")
        return True

    async def _drain_tasks(self) -> None:
        """等待所有任务完成"""
        while True:
            count = await self._state_manager.count_active()
            if count == 0:
                break
            await asyncio.sleep(0.5)

    async def _force_terminate(self) -> None:
        """强制终止所有任务"""
        runs = await self._state_manager.get_all()
        for run in runs:
            if run.state in (RunState.RUNNING, RunState.CANCELLING):
                await self.cancel(run.run_id, reason="force_terminate")

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "running": self._running,
            "polling": self._polling,
            "queue_size": self._scheduler.size,
            "max_concurrent": self._max_concurrent,
        }

    def _generate_run_id(self, task_id: str) -> str:
        return f"run-{task_id}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    def _build_payload(self, task_msg: Any) -> Any:
        """构建任务 payload"""
        from antcode_worker.domain.enums import TaskType
        from antcode_worker.domain.models import TaskPayload

        project_type = getattr(task_msg, "project_type", "code") or "code"
        project_type = str(project_type).lower()
        task_type = {
            "spider": TaskType.SPIDER,
            "render": TaskType.RENDER,
            "code": TaskType.CODE,
            "file": TaskType.CODE,  # 文件项目使用 CODE 插件执行
        }.get(project_type, TaskType.CUSTOM)

        params = getattr(task_msg, "params", {}) or {}
        args = []
        kwargs = {}
        artifact_patterns = []
        if isinstance(params, dict):
            args = params.get("args", []) if isinstance(params.get("args", []), list) else []
            kwargs = params.get("kwargs", {}) if isinstance(params.get("kwargs", {}), dict) else params
            if isinstance(params.get("artifact_patterns"), list):
                artifact_patterns = params.get("artifact_patterns", [])
        elif isinstance(params, list):
            args = params

        env_vars = getattr(task_msg, "environment", {}) or {}
        if isinstance(env_vars, dict) and "ANTCODE_RUNTIME_ENV" in env_vars:
            env_vars = dict(env_vars)
            env_vars.pop("ANTCODE_RUNTIME_ENV", None)

        # 获取 is_compressed 字段（用于判断是否需要解压）
        is_compressed = getattr(task_msg, "is_compressed", None)

        return TaskPayload(
            task_type=task_type,
            project_path=None,
            download_url=getattr(task_msg, "download_url", "") or None,
            file_hash=getattr(task_msg, "file_hash", "") or None,
            is_compressed=is_compressed,
            entry_point=getattr(task_msg, "entry_point", "") or "",
            args=args,
            kwargs=kwargs,
            env_vars=env_vars,
            artifact_patterns=artifact_patterns,
        )

    async def apply_config_update(self, config: dict[str, Any]) -> None:
        """应用资源配置更新"""
        max_concurrent = config.get("max_concurrent_tasks")
        memory_limit_mb = config.get("task_memory_limit_mb")
        cpu_limit_seconds = config.get("task_cpu_time_limit_sec")

        if max_concurrent is not None:
            try:
                new_max = int(max_concurrent)
                if new_max > 0 and new_max != self._max_concurrent:
                    await self._resize_workers(new_max)
            except Exception:
                logger.warning(f"无效的 max_concurrent_tasks: {max_concurrent}")

        if memory_limit_mb is not None:
            try:
                self._policies.resource.memory_limit_mb = int(memory_limit_mb)
            except Exception:
                logger.warning(f"无效的 task_memory_limit_mb: {memory_limit_mb}")

        if cpu_limit_seconds is not None:
            try:
                self._policies.resource.cpu_limit_seconds = int(cpu_limit_seconds)
            except Exception:
                logger.warning(f"无效的 task_cpu_time_limit_sec: {cpu_limit_seconds}")

    async def _resize_workers(self, new_max: int) -> None:
        """动态调整并发 worker 数量"""
        diff = new_max - self._max_concurrent
        if diff == 0:
            return

        self._max_concurrent = new_max
        self._policies.resource.max_concurrent = new_max
        await self._scheduler.update_max_size(new_max * 2)

        if not self._running:
            return

        if diff > 0:
            for _ in range(diff):
                worker_id = len(self._worker_tasks)
                task = asyncio.create_task(self._worker_loop(worker_id))
                self._worker_tasks.append(task)
        else:
            for _ in range(-diff):
                if not self._worker_tasks:
                    break
                task = self._worker_tasks.pop()
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _prepare_runtime(self, context: RunContext) -> Any:
        """准备运行时句柄"""
        runtime_env_name = (context.labels or {}).get("runtime_env_name")
        if runtime_env_name:
            from antcode_worker.domain.models import RuntimeHandle, RuntimeSpec
            from antcode_worker.runtime.uv_manager import uv_manager

            env_info = await uv_manager.get_env(runtime_env_name)
            if not env_info:
                raise RuntimeError(f"运行时环境不存在: {runtime_env_name}")

            context.runtime_spec = RuntimeSpec(
                python_version=env_info.get("python_version"),
                python_path=env_info.get("python_executable"),
            )
            return RuntimeHandle(
                path=env_info.get("path", ""),
                runtime_hash=f"env:{runtime_env_name}",
                python_executable=env_info.get("python_executable", ""),
                python_version=env_info.get("python_version"),
            )

        if not self._runtime_manager or not context.runtime_spec:
            return self._system_runtime_handle()

        from antcode_worker.runtime.spec import RuntimeSpec as RuntimeSpecV2

        spec_data = context.runtime_spec.to_dict() if hasattr(context.runtime_spec, "to_dict") else {}
        spec = RuntimeSpecV2.from_dict(spec_data) if spec_data else RuntimeSpecV2()
        return await self._runtime_manager.prepare(spec)

    def _system_runtime_handle(self) -> Any:
        """构建系统运行时句柄"""
        import sys

        from antcode_worker.domain.models import RuntimeHandle

        return RuntimeHandle(
            path=sys.prefix,
            runtime_hash="system",
            python_executable=sys.executable,
            python_version=sys.version.split()[0],
        )

    def _build_fallback_plan(self, context: RunContext, payload: Any, runtime_handle: Any) -> Any:
        """无插件时的兜底执行计划"""
        from antcode_worker.domain.models import ExecPlan

        command = runtime_handle.python_executable
        args = [payload.entry_point] if payload.entry_point else []

        return ExecPlan(
            command=command,
            args=args,
            env=payload.env_vars,
            cwd=payload.project_path or ".",
            timeout_seconds=context.timeout_seconds,
            memory_limit_mb=context.memory_limit_mb,
            cpu_limit_seconds=context.cpu_limit_seconds,
        )
