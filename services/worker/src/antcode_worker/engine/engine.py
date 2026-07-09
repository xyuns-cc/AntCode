"""
引擎核心

实现任务生命周期管理：poll -> schedule -> execute -> report

Requirements: 4.1, 4.5, 4.6, 4.7, 4.8
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from antcode_core.observability.tracing import (
    child_span,
    new_trace,
    parse_traceparent,
    set_current_trace,
)
from loguru import logger

from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.models import ExecResult, RunContext
from antcode_worker.engine.policies import Policies, default_policies
from antcode_worker.engine.scheduler import Scheduler
from antcode_worker.engine.state import RunState, StateManager
from antcode_worker.transport.base import (
    ControlMessage,
    TaskMessage,
    TransportBase,
)

if TYPE_CHECKING:
    # 仅类型注解使用的依赖：避免运行时 import 引入循环 / 重负载。
    # 这些都是可选依赖，构造时以 ``None`` 兜底。
    from antcode_worker.executor.base import BaseExecutor
    from antcode_worker.runtime.manager import RuntimeManager


# ---------------------------------------------------------------------------
# RuntimeControl 参数读取辅助
#
# 新协议 (control_pb2.RuntimeControl.action_typed.generic.args) 是
# ``map<string, string>``：所有值都是字符串。Direct 模式旧路径仍可能传
# typed dict（list / int / bool 已经是原生 Python 类型）。下面这组帮助
# 函数同时支持两种 shape，保留 engine 现有 action handler 的语义。
# ---------------------------------------------------------------------------
def _arg_str(args: dict, key: str, default: str | None = None) -> str | None:
    value = args.get(key, default)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _arg_bool(args: dict, key: str, default: bool = False) -> bool:
    value = args.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    return bool(value)


def _arg_list(args: dict, key: str, default: list | None = None) -> list:
    """``packages`` / 类似列表参数。

    新协议 map<string,string>：值是 JSON 数组字符串（如 ``'["a","b"]'``）
    或逗号分隔字符串。旧 dict 直接是 ``list``。
    """
    value = args.get(key, default)
    if value is None:
        return list(default) if default else []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        # JSON 数组优先
        if text.startswith("[") and text.endswith("]"):
            with contextlib.suppress(Exception):
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return parsed
        # 退化：逗号分隔
        return [item.strip() for item in text.split(",") if item.strip()]
    if isinstance(value, tuple):
        return list(value)
    return [value]


class Engine:
    """
    引擎核心

    主循环：poll -> schedule -> execute -> report

    Requirements: 4.1, 4.5, 4.6, 4.7, 4.8
    """

    def __init__(
        self,
        transport: TransportBase,
        executor: BaseExecutor,
        flow_controller: Any = None,
        runtime_manager: RuntimeManager | None = None,
        plugin_registry: Any = None,
        log_manager_factory: Any = None,
        project_fetcher: Any = None,
        artifact_manager: Any = None,
        policies: Policies | None = None,
        max_concurrent: int = 5,
        memory_limit_mb: int = 0,
        cpu_limit_seconds: int = 0,
    ):
        # P2-#31: ``transport`` 与 ``executor`` 是核心依赖，按抽象基类标注；
        # 其他 manager / registry 仍保留 Any，因为它们的接口尚未稳定
        # （flow_controller / plugin_registry / log_manager_factory /
        # project_fetcher / artifact_manager 没有统一基类）。
        self._transport: TransportBase = transport
        self._executor: BaseExecutor = executor
        self._flow_controller = flow_controller
        self._runtime_manager: RuntimeManager | None = runtime_manager
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
        # P2-#37: bounded set 防止 ``_handle_runtime_control`` 派出的 task
        # 被 GC 回收（asyncio.create_task 返回值丢弃会触发警告，长任务可能
        # 被取消）；done_callback 自动从集合移除。
        self._inflight_controls: set[asyncio.Task] = set()

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

                # B2: 本地去重 —— state_manager.add_if_new 报告已存在时跳过 enqueue，
                # 避免 reclaim / direct 重投时同一 run_id 排入两次本地队列。
                _, is_new_local = await self._state_manager.add_if_new(
                    run_id, task_msg.task_id, receipt=task_msg.receipt
                )
                if not is_new_local:
                    logger.warning(
                        f"跳过重复投递（本地已存在 run）: run_id={run_id} task_id={task_msg.task_id}"
                    )
                    # ACK 掉重复消息避免占 PEL
                    receipt = getattr(task_msg, "receipt", None)
                    if receipt:
                        try:
                            # R1-P1-4 (审查报告): ack_task 必须传 receipt（含
                            # stream_key|msg_id），传 task_id 会走 _decode_receipt
                            # 的 ``if "|" not in receipt: return "", ""`` 静默失败
                            await self._transport.ack_task(receipt, accepted=True)
                        except Exception as ack_exc:
                            logger.warning(f"跳过重复投递后 ack 失败: {ack_exc}")
                    continue

                # B2 跨机 fencing：SET NX processing:{run_id} 抢占，防止 reclaim
                # 把消息交给另一台 worker 时两台同时跑。Redis 不可达时保守放行
                # 只依赖本地去重（单节点部署时天然安全）。
                if not await self._claim_run_ownership(run_id):
                    logger.warning(
                        f"跳过重复投递（其它 worker 已持有 run）: run_id={run_id}"
                    )
                    # 从本地状态清理（我们没真的接手）；ACK 掉不属于自己的消息避免死锁
                    try:
                        await self._state_manager.remove(run_id)
                    except Exception:
                        pass
                    receipt = getattr(task_msg, "receipt", None)
                    if receipt:
                        try:
                            # R1-P1-4 (审查报告): ack_task 必须传 receipt（含
                            # stream_key|msg_id），传 task_id 会走 _decode_receipt
                            # 的 ``if "|" not in receipt: return "", ""`` 静默失败
                            await self._transport.ack_task(receipt, accepted=True)
                        except Exception as ack_exc:
                            logger.warning(f"跨机去重后 ack 失败: {ack_exc}")
                    continue

                # 入队
                await self._scheduler.enqueue(
                    run_id=run_id,
                    data=(context, task_msg),
                    priority=task_msg.priority,
                )

                logger.info(f"任务入队: {run_id}")

            except asyncio.CancelledError:
                break
            except Exception:
                if self._flow_controller:
                    self._flow_controller.on_failure()
                logger.exception("轮询异常")
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
                    # P2-#37: 保持强引用避免 task 被 GC；done_callback 自动清理。
                    task = asyncio.create_task(
                        self._handle_runtime_control(control)
                    )
                    self._inflight_controls.add(task)
                    task.add_done_callback(self._inflight_controls.discard)
                    continue

                if control.receipt:
                    await self._transport.ack_control(control.receipt)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("控制通道异常")
                await asyncio.sleep(1)

    # ------------------------------------------------------------------
    # RuntimeControl action handlers
    #
    # P2-#32: 之前 ``_handle_runtime_control`` 是一条 14-elif 长链，路由 +
    # 参数解码 + 业务调用混在一起。现在拆成：
    #   * ``_ACTION_HANDLERS`` 静态表 —— 路由 dispatch
    #   * 每个 ``_action_*`` 方法 —— 参数解析 + uv_manager 调用
    #   * 上层只剩 dispatch + 信号量 + 异常归一
    # 新增 action 时只需写一个 ``_action_xxx`` 方法并在表里登记。
    # ------------------------------------------------------------------
    async def _action_list_envs(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
        scope = _arg_str(data, "scope") or None
        return await uv_manager.list_envs(scope=scope)

    async def _action_get_env(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
        env_name = _arg_str(data, "env_name")
        if not env_name:
            raise RuntimeError("env_name 不能为空")
        result = await uv_manager.get_env(env_name)
        if result is None:
            raise RuntimeError("环境不存在")
        return result

    async def _action_update_env(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
        env_name = _arg_str(data, "env_name")
        if not env_name:
            raise RuntimeError("env_name 不能为空")
        return await uv_manager.update_env(
            env_name=env_name,
            key=_arg_str(data, "key"),
            description=_arg_str(data, "description"),
        )

    async def _action_create_env(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
        env_name = _arg_str(data, "env_name")
        if not env_name:
            raise RuntimeError("env_name 不能为空")
        return await uv_manager.create_env(
            env_name=env_name,
            python_version=_arg_str(data, "python_version"),
            packages=_arg_list(data, "packages"),
            created_by=_arg_str(data, "created_by") or None,
        )

    async def _action_delete_env(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
        env_name = _arg_str(data, "env_name")
        if not env_name:
            raise RuntimeError("env_name 不能为空")
        deleted = await uv_manager.delete_env(env_name)
        return {"deleted": bool(deleted)}

    async def _action_list_packages(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
        env_name = _arg_str(data, "env_name")
        if not env_name:
            raise RuntimeError("env_name 不能为空")
        return await uv_manager.list_packages(env_name)

    async def _action_install_packages(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
        env_name = _arg_str(data, "env_name")
        packages = _arg_list(data, "packages")
        if not env_name or not packages:
            raise RuntimeError("env_name 和 packages 不能为空")
        return await uv_manager.install_packages(
            env_name=env_name,
            packages=packages,
            upgrade=_arg_bool(data, "upgrade", False),
        )

    async def _action_uninstall_packages(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
        env_name = _arg_str(data, "env_name")
        packages = _arg_list(data, "packages")
        if not env_name or not packages:
            raise RuntimeError("env_name 和 packages 不能为空")
        return await uv_manager.uninstall_packages(
            env_name=env_name,
            packages=packages,
        )

    async def _action_list_interpreters(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
        return await uv_manager.list_all_interpreters()

    async def _action_install_interpreter(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
        version = _arg_str(data, "version")
        if not version:
            raise RuntimeError("version 不能为空")
        return await uv_manager.install_interpreter(version)

    async def _action_uninstall_interpreter(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
        version = _arg_str(data, "version")
        if not version:
            raise RuntimeError("version 不能为空")
        return await uv_manager.uninstall_interpreter(version)

    async def _action_register_interpreter(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
        python_bin = _arg_str(data, "python_bin")
        if not python_bin:
            raise RuntimeError("python_bin 不能为空")
        return await uv_manager.register_interpreter(
            python_bin=python_bin,
            version=_arg_str(data, "version") or None,
        )

    async def _action_unregister_interpreter(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
        return await uv_manager.unregister_interpreter(
            python_bin=_arg_str(data, "python_bin") or None,
            version=_arg_str(data, "version") or None,
        )

    async def _action_get_python_versions(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
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
        return {
            "installed": installed,
            "available": available,
            "all_interpreters": all_interpreters,
            "platform": platform_info,
        }

    async def _action_get_platform_info(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager
        return await uv_manager.get_platform_info_async()

    # 静态路由表：``action`` 字符串 → ``Engine`` 实例方法。
    # ``_handle_runtime_control`` 根据 action 查表，找不到就报 unknown action。
    _ACTION_HANDLERS: dict[
        str,
        Callable[[Engine, dict], Awaitable[Any]],
    ] = {
        "list_envs": _action_list_envs,
        "get_env": _action_get_env,
        "update_env": _action_update_env,
        "create_env": _action_create_env,
        "delete_env": _action_delete_env,
        "list_packages": _action_list_packages,
        "install_packages": _action_install_packages,
        "uninstall_packages": _action_uninstall_packages,
        "list_interpreters": _action_list_interpreters,
        "install_interpreter": _action_install_interpreter,
        "uninstall_interpreter": _action_uninstall_interpreter,
        "register_interpreter": _action_register_interpreter,
        "unregister_interpreter": _action_unregister_interpreter,
        "get_python_versions": _action_get_python_versions,
        "get_platform_info": _action_get_platform_info,
    }

    async def _handle_runtime_control(self, control: ControlMessage) -> None:
        """处理运行时管理控制消息

        新协议 (control_pb2.RuntimeControl) 字段抽取规则：
        - ``request_id`` / ``action`` 从 ControlMessage.payload 顶层读
        - ``args`` 是 typed map<string,string>（GenericAction.args），
          通过 ``_arg_*`` 帮助函数解码为原 dict 行为兼容的类型
        - 旧路径还有 ``payload`` / ``reply_stream`` 字段，这里 fallback 读
          ``payload`` 保持兼容（Direct 模式 P3 收尾前仍按 dict 走）
        - ``reply_stream`` 在新协议下已废弃 — 结果通过 ``ack_control``
          （携带 success/error）回报

        P2-#32: 路由从 14 elif 链改为 ``_ACTION_HANDLERS`` 表驱动。
        """
        payload = control.payload or {}
        action = payload.get("action", "")
        request_id = payload.get("request_id", "")
        # 新协议优先用 typed args；旧路径 fallback 到 payload 嵌套 dict
        data = payload.get("args") or payload.get("payload") or {}

        handler = self._ACTION_HANDLERS.get(action)

        success = True
        result_data: Any = None
        error_message = ""

        try:
            if handler is None:
                raise RuntimeError(f"未知运行时操作: {action}")
            async with self._runtime_control_semaphore:
                result_data = await handler(self, data)
        except Exception as e:
            success = False
            error_message = str(e)
            # P2: ``except Exception`` 块统一用 logger.exception 保留堆栈，
            # 避免错误被静默吞掉只剩 ``str(e)``。
            logger.exception(
                f"runtime action 失败: action={action} req={request_id}"
            )
        finally:
            # 新协议：结果通过 ``send_control_result`` 回报。
            # Gateway 模式下它实际走 ``ControlService.AckControl(success, error)``，
            # 不再需要 reply_stream 字段。Direct 模式下仍写 reply Stream（P3 收尾）。
            # 兼容旧 Direct 路径：如果 payload 里还带 ``reply_stream``，透传过去。
            if request_id:
                reply_stream = payload.get("reply_stream", "") or payload.get("params", {}).get("reply_stream", "")
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

                # P5.4: 把 TaskDispatch 携带的 traceparent 绑定到当前
                # asyncio.Task 的 ContextVar。一旦绑定,后续 logger 调用
                # 和 transport.report_result / send_log_batch 等出站点都
                # 会自动透传同一个 trace,实现 Master ↔ Worker 端到端链路。
                #
                # transport 层在 poll_task 后会把 dispatch.trace.traceparent
                # 用 setattr 挂到 task_msg 上(TaskMessage dataclass 没有
                # traceparent 字段,但 Python 允许动态属性)。拿不到时新
                # 起一个 trace,保证当前任务内 logger 仍有 trace_id 可贴。
                inbound_traceparent = getattr(task_msg, "traceparent", "") or ""
                if inbound_traceparent:
                    # 从父 traceparent 派生子 span: 同一 trace_id,新 span_id
                    set_current_trace(child_span(inbound_traceparent).traceparent)
                else:
                    set_current_trace(new_trace().traceparent)

                # 执行任务
                result = await self._execute_task(context, task_msg)

                # 上报结果
                await self._report_result(context, result)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(f"Worker-{worker_id} 异常")

    async def _execute_task(self, context: RunContext, task_msg: TaskMessage) -> ExecResult:
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
            payload.run_id = run_id
            payload.project_id = context.project_id

            # V1/V1.5/V2: 下载/缓存项目 source bundle
            source_bundle = getattr(task_msg, "source_bundle", None)
            if self._project_fetcher and source_bundle is not None:
                workspace = await self._project_fetcher.fetch(
                    run_id=run_id,
                    project_id=context.project_id,
                    source_bundle_uri=source_bundle.uri,
                    source_bundle_sha256=source_bundle.sha256,
                    source_bundle_size=source_bundle.size or 0,
                    entry_point=payload.entry_point,
                    source_subdir=getattr(source_bundle, "source_subdir", None)
                    or getattr(task_msg, "source_subdir", "")
                    or "",
                )
                # V5: SpiderPlugin / 其它插件读 workspace_path / project_cwd
                payload.workspace_path = workspace.bundle_root or ""
                payload.project_cwd = workspace.project_cwd or workspace.bundle_root or ""

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

            # V11: 把入站 traceparent 透传给子进程,实现 Master → Worker → 子进程
            # 端到端的 trace_id 连续。子脚本(scrapy/spider/用户代码)读取
            # TRACEPARENT / ANTCODE_TRACE_ID 即可注入自己的 logger。
            inbound_traceparent = getattr(task_msg, "traceparent", "") or ""
            if inbound_traceparent:
                exec_plan.env["TRACEPARENT"] = inbound_traceparent
                ids = parse_traceparent(inbound_traceparent)
                exec_plan.env["ANTCODE_TRACE_ID"] = ids.trace_id if ids else ""

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
            logger.exception(f"执行失败: {run_id}")
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
            # R1-P1-6 (审查报告): 隔离 log_manager.stop 异常。老实现里
            # LogManager.stop() → BatchSender.flush() → 失败 raise RuntimeError
            # 会替换 try 块的 return 值，被外层 _worker_loop 兜底吞掉，
            # 结果永远不上报也不 ACK，任务在 master 侧卡永远 running。
            # 现在 stop 失败降级为告警，不影响结果上报。
            if log_manager:
                try:
                    await log_manager.stop()
                except Exception as exc:
                    logger.warning(f"log_manager.stop 失败但不影响结果上报: {exc}")
            if runtime_handle and self._runtime_manager:
                await self._runtime_manager.release(runtime_handle)
            # V3: 清理 fetched workspace,避免无限堆积
            if self._project_fetcher is not None:
                try:
                    await self._project_fetcher.cleanup(run_id)
                except Exception:
                    logger.exception(f"清理 workspace 失败: run_id={run_id}")
            # R5-P2-6: rule 插件在无 workspace 时会把 rule JSON 写到
            # `/tmp/antcode-rule/{run_id}/`（plugin.py:_resolve_rule_dir 的
            # fallback 分支），fetcher.cleanup 只清 workspace 目录管不到这里。
            # 长跑几天后 /tmp 会堆一堆 rule-*.json 遗骸。这里按约定路径清一
            # 下——只清 run_id 子目录，不动 `/tmp/antcode-rule/` 根，避免并发
            # run 相互踩。
            await self._cleanup_rule_tmp(run_id)

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
        report_ok = await self._transport.report_result(task_result)
        if report_ok:
            logger.info(f"结果已上报: {context.run_id}")
            # V12: 只有 report_result 成功才 ack;
            # 失败时不 ack,Stream 上的 PEL 会被 XAUTOCLAIM 回收交给其它 worker 重试。
            if context.receipt:
                await self._transport.ack_task(context.receipt, accepted=True)
            # 清理状态(成功路径)
            await self._state_manager.remove(context.run_id)
            # B2: 释放跨机归属键，让后续同 run_id 重投（如极端情况下的手动重试）不被锁死
            await self._release_run_ownership(context.run_id)
        else:
            logger.error(
                f"report_result 失败,不 ack 让 PEL 自动 reclaim: run_id={context.run_id}"
            )
            # R1-P1-5 (审查报告): 上报失败必须清理本地 RunInfo。原实现只
            # release_run_ownership 但不 remove 本地 state → direct 模式
            # ready stream 是 per-worker，reclaim 后消息还是回到同一 worker，
            # `add_if_new` 在 state 里已存在就直接吞掉重投，结果永久丢失。
            try:
                await self._state_manager.remove(context.run_id)
            except Exception as exc:
                logger.debug(f"清理本地 state 失败（可忽略）: {exc}")
            # 释放本地归属，交给 reclaim 到别的 worker 重试。B2 的跨机 fencing
            # 在 TTL 内保留，防止老 worker 复活后又抢——由新 worker 的 SET NX 决定归属。
            await self._release_run_ownership(context.run_id)

    async def _cleanup_rule_tmp(self, run_id: str) -> None:
        """R5-P2-6: rule plugin fallback 路径 `/tmp/antcode-rule/{run_id}/` 兜底清理。

        与 rule/plugin.py::_resolve_rule_dir 的兜底约定一致。只删 run_id
        子目录，不 rm -rf 上层 `/tmp/antcode-rule/`（并发 run 会共享该父
        目录）。用 to_thread 避免阻塞 event loop。
        """
        if not run_id:
            return
        rule_run_dir = os.path.join(tempfile.gettempdir(), "antcode-rule", run_id)
        try:
            if os.path.isdir(rule_run_dir):
                await asyncio.to_thread(shutil.rmtree, rule_run_dir, ignore_errors=True)
        except Exception as exc:
            # 清理失败不影响主流程；下一轮 tmp cleanup 或系统 tmp 清理会兜住
            logger.debug(f"清理 rule tmp 目录失败: {rule_run_dir}: {exc}")

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

    # B2: 跨机 run 归属期（秒）。任务实际执行时长 ≤ TASK_EXECUTION_TIMEOUT，
    # 归属期 = timeout + 冗余；正常 ack 或结果回传时会 DEL，Redis 侧不会长期占用。
    _RUN_OWNERSHIP_TTL_SECONDS = 3600 + 300

    async def _claim_run_ownership(self, run_id: str) -> bool:
        """B2 跨机去重：SET NX 抢占 ``processing:{run_id}``。

        - 成功 → 只有我在跑这个 run。
        - 失败 → 别的 worker（reclaim / direct 重投另一台）已持有，跳过。
        - Redis 不可达 → 保守返回 True 让本地去重继续起作用（单 worker 安全，
          多 worker 场景由运维监控，不做静默双跑）。
        """
        try:
            from antcode_core.infrastructure.redis import get_redis_client

            redis = await get_redis_client()
            if redis is None:
                logger.debug("Redis 不可达，跳过跨机去重（放行本地已去重的消息）")
                return True
            key = f"antcode:run:owner:{run_id}"
            worker_id = getattr(self._transport, "worker_id", "") or "unknown"
            ok = await redis.set(key, worker_id, nx=True, ex=self._RUN_OWNERSHIP_TTL_SECONDS)
            return bool(ok)
        except Exception as exc:
            logger.warning(f"跨机去重 SET NX 失败(保守放行): run_id={run_id} err={exc}")
            return True

    async def _release_run_ownership(self, run_id: str) -> None:
        """任务完成后释放跨机归属键。仅当 key 值为我方 worker_id 时才 DEL（防误删）。"""
        try:
            from antcode_core.infrastructure.redis import get_redis_client

            redis = await get_redis_client()
            if redis is None:
                return
            key = f"antcode:run:owner:{run_id}"
            worker_id = getattr(self._transport, "worker_id", "") or "unknown"
            # Lua 保证 GET+DEL 原子
            script = """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            else
                return 0
            end
            """
            await redis.eval(script, 1, key, worker_id)
        except Exception as exc:
            logger.debug(f"释放跨机归属键失败(TTL 会兜底): run_id={run_id} err={exc}")

    def _build_payload(self, task_msg: TaskMessage) -> Any:
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
            "rule": TaskType.RULE,  # O1: 规则项目走 RulePlugin
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

        return TaskPayload(
            task_type=task_type,
            source_bundle=getattr(task_msg, "source_bundle", None),
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
            cwd=payload.project_cwd or payload.workspace_path or ".",
            timeout_seconds=context.timeout_seconds,
            memory_limit_mb=context.memory_limit_mb,
            cpu_limit_seconds=context.cpu_limit_seconds,
        )
