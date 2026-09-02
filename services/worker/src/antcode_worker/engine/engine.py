"""Worker 任务生命周期引擎：poll -> schedule -> execute -> report。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any

from antcode_contracts.runtime_metadata import validate_runtime_creator, validate_runtime_metadata
from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.observability.tracing import (
    child_span,
    new_trace,
    parse_traceparent,
    set_current_trace,
)
from loguru import logger

from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.errors import CancellationError
from antcode_worker.domain.models import ExecPlan, ExecResult, RunContext
from antcode_worker.engine.cancel_tombstones import CancelTombstones
from antcode_worker.engine.cancellation import cancel_queued_run, cancel_started_run
from antcode_worker.engine.capacity_update import apply_capacity_limits
from antcode_worker.engine.config_update import CurrentCapacity, resolve_engine_config_update
from antcode_worker.engine.execution_admission import execute_with_admission, execution_egress
from antcode_worker.engine.execution_completion import complete_execution
from antcode_worker.engine.execution_failure import handle_execution_failure
from antcode_worker.engine.fatal_error import FatalErrorMixin
from antcode_worker.engine.metrics_recorders import WorkerMetricsRecorderMixin
from antcode_worker.engine.ownership_fence import (
    FatalErrorSignal,
    OwnershipFenceError,
    abort_for_ownership_failure,
    cancel_executor_run,
    run_with_generation_fence,
)
from antcode_worker.engine.policies import Policies, default_policies
from antcode_worker.engine.poll_delivery_recovery import handle_poll_failure
from antcode_worker.engine.preparation_tasks import PreparationCancelledError, PreparationTaskRegistry
from antcode_worker.engine.released_ownership import ReleasedOwnershipLedger
from antcode_worker.engine.runtime_control_guard import _require_live_runtime_control, runtime_action_failure
from antcode_worker.engine.scheduler import Scheduler
from antcode_worker.engine.shutdown import stop_engine
from antcode_worker.engine.spider_spool import relay_spider_spool
from antcode_worker.engine.state import RunState, StateManager
from antcode_worker.executor.rule_policy import RULE_PLUGIN_ENV_VARS
from antcode_worker.transport.base import (
    ControlMessage,
    TaskMessage,
    TransportBase,
)
from antcode_worker.transport.generation import raise_if_generation_lost

if TYPE_CHECKING:
    from antcode_worker.executor.base import BaseExecutor
    from antcode_worker.runtime.manager import RuntimeManager
    from antcode_worker.transport.base import TaskResult

RUNTIME_RESULT_RETRY_MAX_SECONDS = 30.0
MILLISECONDS_PER_SECOND = 1_000


@dataclass(frozen=True)
class _RuntimeControlResult:
    request_id: str
    reply_stream: str
    success: bool
    receipt: str
    data: Any
    error: str


# RuntimeControl 参数读取辅助：新协议
# (control_pb2.RuntimeControl.action_typed.generic.args) 是 ``map<string, string>``，
# 值全是字符串；Direct 旧路径仍可能传 typed dict（list / int / bool 是原生类型）。
# 下面这组函数同时吃两种 shape。
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
        if text.startswith("[") and text.endswith("]"):
            with contextlib.suppress(Exception):
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return parsed
        return [item.strip() for item in text.split(",") if item.strip()]
    if isinstance(value, tuple):
        return list(value)
    return [value]


class Engine(FatalErrorMixin, WorkerMetricsRecorderMixin):
    """引擎核心。Requirements: 4.1, 4.5, 4.6, 4.7, 4.8"""

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
        *,
        cancel_tombstones: CancelTombstones | None = None,
        auto_resource_limit: bool = False,
        adaptive_limits_provider: Callable[[int | None], dict[str, int]] | None = None,
        capacity_observer: Callable[[int], None] | None = None,
    ):
        # 其余 manager / registry 保留 Any 是因为它们没有统一基类，不是漏标。
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
        self._auto_resource_limit = auto_resource_limit
        self._adaptive_limits_provider = adaptive_limits_provider
        self._capacity_observer = capacity_observer

        self._scheduler = Scheduler(max_queue_size=max_concurrent * 2)
        self._state_manager = StateManager()
        # tombstone 键要带 ns/wid 维度才能落进本 Worker 的最小权限 ACL 面，
        # 故由 wiring 注入，引擎不自行拼装 Redis 键。
        self._cancel_tombstones = cancel_tombstones or CancelTombstones()

        self._running = False
        self._polling = False
        self._poll_task: asyncio.Task | None = None
        self._control_task: asyncio.Task | None = None
        self._worker_tasks: list[asyncio.Task] = []
        self._worker_shrink_tasks: set[asyncio.Task] = set()
        self._next_worker_id = 0
        self._runtime_control_semaphore = asyncio.Semaphore(1)
        # 持有强引用防止 ``_handle_runtime_control`` 派出的 task 被 GC 回收
        # （丢弃 create_task 返回值时长任务可能被取消）；done_callback 负责移除。
        self._inflight_controls: set[asyncio.Task] = set()
        self._runtime_control_tasks: dict[str, asyncio.Task] = {}
        self._runtime_control_receipts: dict[asyncio.Task, str] = {}

        self._policies.resource.max_concurrent = max_concurrent
        self._policies.resource.memory_limit_mb = memory_limit_mb
        self._policies.resource.cpu_limit_seconds = cpu_limit_seconds

        self._worker_id_cache: str | None = None
        self._ownership_renewal_task: asyncio.Task | None = None
        self._ownership_renew_wakeup: asyncio.Event | None = None  # P1-GW-02
        self._fatal_error_signal = FatalErrorSignal()
        self._ownership_fenced = False
        self._released_ownership = ReleasedOwnershipLedger()

        # 缩容用的 drain 集合，三段式关闭见 ``_shrink_workers``。
        self._draining_worker_tasks: set[asyncio.Task] = set()
        self._worker_run_ids: dict[asyncio.Task, str] = {}
        self._forced_cancel_relay_errors: dict[str, str] = {}
        # W2: relay 未能在硬性时限内退出的 run，跳过其 rule tmp rmtree，
        # 避免 _cleanup_rule_tmp 与仍在读 spool 的 relay 竞态。
        self._deferred_rule_tmp_cleanup: set[str] = set()
        self._preparation_tasks = PreparationTaskRegistry()

    @property
    def scheduler(self) -> Scheduler:
        return self._scheduler

    @property
    def state_manager(self) -> StateManager:
        return self._state_manager

    async def start(self) -> None:
        if self._running:
            return

        resolved = self._resolve_worker_id()
        logger.debug(f"engine 已解析 worker_id: {resolved}")
        register = getattr(self._transport, "set_lease_revoked_callback", None)
        if callable(register):
            register(self._on_transport_lease_revoked)
        self._running = True
        self._polling = True
        self._ownership_fenced = False
        self._fatal_error_signal.reset()

        await self._scheduler.start()

        self._poll_task = asyncio.create_task(self._poll_loop())
        self._control_task = asyncio.create_task(run_with_generation_fence(self, self._control_loop))

        for _ in range(self._max_concurrent):
            self._worker_tasks.append(self._create_worker_task())

        # 续租跨机归属键，否则长跑任务 TTL 到期后会被重投到另一台。
        self._ownership_renewal_task = asyncio.create_task(self._renew_run_ownership_loop())

        logger.info(f"引擎已启动 (workers={self._max_concurrent})")

    async def stop(self, grace_period: float = 30.0) -> None:
        """停止 intake，等待 Engine 所有协程完成清理后返回。"""
        await stop_engine(self, grace_period)

    async def _poll_loop(self) -> None:
        while self._polling:
            flow_acquired = False
            uncommitted_receipt: str | None = None
            admitted_run_id: str | None = None
            try:
                if not self._transport or not self._transport.is_connected:
                    await asyncio.sleep(0.5)
                    continue

                if self._scheduler.is_full:
                    await asyncio.sleep(1)
                    continue

                if self._flow_controller:
                    flow_acquired = await self._flow_controller.acquire(timeout=self._policies.timeout.poll_timeout)
                    if not flow_acquired:
                        await asyncio.sleep(0.1)
                        continue

                task_msg = await self._transport.poll_task(timeout=self._policies.timeout.poll_timeout)
                if self._flow_controller:
                    self._flow_controller.on_success()

                if task_msg is None:
                    continue
                uncommitted_receipt = getattr(task_msg, "receipt", None)

                labels = {}
                runtime_env_name = getattr(task_msg, "runtime_env_name", "") or ""
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

                if not await self._admit_polled_task(run_id, task_msg):
                    uncommitted_receipt = None
                    continue
                admitted_run_id = run_id

                await self._scheduler.enqueue(
                    run_id=run_id,
                    data=(context, task_msg),
                    priority=task_msg.priority,
                )
                uncommitted_receipt = None

                logger.info(f"任务入队: {run_id}")

            except asyncio.CancelledError:
                break
            except Exception as exc:
                stop_polling = await handle_poll_failure(
                    engine=self,
                    error=exc,
                    receipt=uncommitted_receipt,
                    admitted_run_id=admitted_run_id,
                )
                if stop_polling:
                    break
            finally:
                if self._flow_controller and flow_acquired:
                    await self._flow_controller.release()

    async def _admit_polled_task(self, run_id: str, task_msg: Any) -> bool:
        """poll 准入：tombstone → 去重 → ownership fence；False=已按各自语义处置。"""
        # 取消先于任务到达时 cancel() 记了 tombstone；命中即按 CANCELLED 结算并
        # ACK，任务绝不执行。
        if await self._cancel_tombstones.consume(run_id):
            await self._settle_tombstoned_task(run_id, task_msg)
            return False
        # 活跃执行只去重；执行已结束的重投取得结算恢复权，绝不重跑业务。
        _, is_new_local = await self._state_manager.add_if_new(run_id, task_msg.task_id, receipt=task_msg.receipt)
        if not is_new_local:
            recover = await self._state_manager.reserve_settlement_recovery(run_id, task_msg.receipt)
            if recover:
                logger.info(f"任务重投进入结算恢复: run_id={run_id} task_id={task_msg.task_id}")
                return True
            logger.warning(f"跳过重复投递（本地已存在 run）: run_id={run_id} task_id={task_msg.task_id}")
            return False

        try:
            ownership_acquired = await self._claim_run_ownership(run_id)
        except Exception:
            await self._state_manager.remove(run_id)
            raise
        if ownership_acquired:
            return True
        logger.warning(f"跳过重复投递（其它 worker 已持有 run）: run_id={run_id}")
        with contextlib.suppress(Exception):
            await self._state_manager.remove(run_id)
        receipt = getattr(task_msg, "receipt", None)
        if receipt:
            # ownership contention 不能 ACK（owner 可能已崩溃）也不能按业务
            # reject（耗尽 requeue 次数进 DLQ）；保留 PEL 等 visibility 重投。
            deferred = await self._transport.defer_task(receipt, reason=f"ownership_contention run_id={run_id}")
            if not deferred:
                raise RuntimeError(f"ownership contention defer 失败: run_id={run_id}")
        return False

    async def _control_loop(self) -> None:
        while self._running:
            try:
                if not self._transport or not self._transport.is_connected:
                    await asyncio.sleep(0.5)
                    continue
                control = await self._transport.poll_control(timeout=self._policies.timeout.poll_timeout)
                if control is None:
                    continue
                await self._dispatch_control(control)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                raise_if_generation_lost(exc)
                logger.exception("控制通道异常")
                await asyncio.sleep(1)

    async def _dispatch_control(self, control: ControlMessage) -> None:
        """按 control_type 分派并 ACK；runtime_manage 异步执行，ACK 由它自己发。"""
        if control.control_type in ("cancel", "kill"):
            await self._invoke_cancel_control(control)
        elif control.control_type == "config_update":
            await self.apply_config_update(control.payload or {})
        elif control.control_type == "runtime_manage":
            self._schedule_runtime_control(control)
            return
        if control.receipt:
            await self._require_control_ack(control.receipt)

    async def _require_control_ack(self, receipt: str) -> None:
        if not await self._transport.ack_control(receipt):
            raise RuntimeError(f"控制事件 ACK 失败: receipt={receipt}")

    async def _invoke_cancel_control(self, control: ControlMessage) -> None:
        """执行幂等取消；真实执行器失败由异常阻止 control ACK。"""
        target = control.run_id or control.task_id
        if not target:
            logger.warning("cancel/kill control 缺 target: type={}", control.control_type)
            return
        cancel_ok = await self.cancel(target, reason=control.reason or control.control_type)
        if not cancel_ok:
            logger.info("cancel/kill 对已终态 run no-op: target={}", target)

    def _schedule_runtime_control(self, control: ControlMessage) -> bool:
        receipt = control.receipt
        if not receipt:
            raise RuntimeError("运行时控制事件缺少 receipt")
        if receipt in self._runtime_control_tasks:
            logger.warning("忽略重复的运行时控制投递: receipt={}", receipt)
            return False
        operation = run_with_generation_fence(self, lambda: self._handle_runtime_control(control))
        task = asyncio.create_task(operation)
        self._inflight_controls.add(task)
        self._runtime_control_tasks[receipt] = task
        self._runtime_control_receipts[task] = receipt
        task.add_done_callback(self._runtime_control_done)
        return True

    def _runtime_control_done(self, task: asyncio.Task) -> None:
        """Observe background failures while preserving retry-by-no-ACK semantics."""
        self._inflight_controls.discard(task)
        receipt = self._runtime_control_receipts.pop(task, None)
        if receipt and self._runtime_control_tasks.get(receipt) is task:
            self._runtime_control_tasks.pop(receipt)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.opt(exception=error).error("运行时控制后台任务失败")

    async def _drain_runtime_controls(self, grace_period: float) -> None:
        pending = {task for task in self._inflight_controls if not task.done()}
        if not pending:
            return
        done, pending = await asyncio.wait(pending, timeout=grace_period)
        _ = done
        if not pending:
            return
        logger.error("运行时控制停止等待超时: pending={}", len(pending))
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        raise TimeoutError(f"运行时控制停止等待超时: pending={len(pending)}")

    # RuntimeControl action handlers：新增 action = 写一个 ``_action_*`` 方法并在
    # ``_ACTION_HANDLERS`` 里登记，不要再往调用方加分支。
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
        key, description = validate_runtime_metadata(
            _arg_str(data, "key"),
            _arg_str(data, "description"),
        )
        return await uv_manager.update_env(
            env_name=env_name,
            key=key,
            description=description,
        )

    async def _action_create_env(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager

        env_name = _arg_str(data, "env_name")
        if not env_name:
            raise RuntimeError("env_name 不能为空")
        created_by, owner_user_id = validate_runtime_creator(
            _arg_str(data, "created_by") or None,
            _arg_str(data, "owner_user_id") or None,
        )
        return await uv_manager.create_env(
            env_name=env_name,
            python_version=_arg_str(data, "python_version"),
            packages=_arg_list(data, "packages"),
            created_by=created_by,
            owner_user_id=owner_user_id,
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

    async def _action_get_platform_info(self, data: dict) -> Any:
        from antcode_worker.runtime.uv_manager import uv_manager

        return await uv_manager.get_platform_info_async()

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
        "get_platform_info": _action_get_platform_info,
    }

    async def _handle_runtime_control(self, control: ControlMessage) -> None:
        """处理运行时管理控制消息。

        字段抽取：``request_id`` / ``action`` 在 payload 顶层；``args`` 是新协议的
        typed ``map<string,string>``，Direct 旧路径退回嵌套 ``payload`` dict。
        Gateway 侧结果通过 ``AckControl`` 回报，reply stream 由 Gateway 从原始受
        认证事件派生——Worker 不得自己指定 Redis key。
        """
        payload = control.payload or {}
        action = payload.get("action", "")
        request_id = payload.get("request_id", "")
        if not control.receipt:
            # 无 receipt 的事件没有 settlement 通路，只能 fail-fast。
            raise RuntimeError("运行时控制事件缺少 receipt")
        if not request_id:
            logger.warning(
                "丢弃缺少 request_id 的运行时控制事件: action={} receipt={}",
                action,
                control.receipt,
            )
            try:
                await self._require_control_ack(control.receipt)
            except Exception:
                logger.exception("畸形运行时控制事件 ACK 失败，等待重投后重试")
                raise
            return
        data = payload.get("args") or payload.get("payload") or {}

        handler = self._ACTION_HANDLERS.get(action)

        success = True
        result_data: Any = None
        error_message = ""

        try:
            _require_live_runtime_control(payload, await self._transport.authoritative_now_ms())
            if handler is None:
                raise RuntimeError(f"未知运行时操作: {action}")
            async with self._runtime_control_semaphore:
                _require_live_runtime_control(payload, await self._transport.authoritative_now_ms())
                result_data = await handler(self, data)
        except Exception as e:
            raise_if_generation_lost(e)
            success = False
            error_message = normalize_persisted_error_message(e) or ""
            result_data = runtime_action_failure(e, action=action, request_id=request_id)

        # CancelledError 会在到达这里前向上传播，事件保持未 settlement。
        reply_stream = payload.get("reply_stream", "") or payload.get("params", {}).get("reply_stream", "")
        result = _RuntimeControlResult(
            request_id=request_id,
            reply_stream=reply_stream,
            success=success,
            receipt=control.receipt,
            data=result_data,
            error=error_message,
        )
        await self._commit_runtime_control_result(result)

    async def _commit_runtime_control_result(self, result: _RuntimeControlResult) -> None:
        retry_delay = 1.0
        while True:
            sent = await self._transport.send_control_result(
                request_id=result.request_id,
                reply_stream=result.reply_stream,
                success=result.success,
                receipt=result.receipt,
                data=result.data,
                error=result.error,
            )
            if sent:
                return
            if not self._running:
                raise RuntimeError(f"控制结果发送失败: request_id={result.request_id}")
            logger.error("控制结果提交失败，将重试: request_id={}", result.request_id)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, RUNTIME_RESULT_RETRY_MAX_SECONDS)

    async def _worker_loop(self, worker_id: int) -> None:
        logger.debug(f"Worker-{worker_id} 启动")
        my_task = asyncio.current_task()

        while self._running:
            # 缩容优雅退出：drain 标记只在轮次起点生效，让在途任务先跑完。
            if my_task is not None and my_task in self._draining_worker_tasks:
                self._draining_worker_tasks.discard(my_task)
                logger.info(f"Worker-{worker_id} drain 完成, 优雅退出")
                return

            # 必须声明在 try 外：except CancelledError 分支要靠它触发
            # executor.cancel + 上报 CANCELLED，否则子进程孤儿化 + master 永卡
            # DISPATCHING。
            active_context: RunContext | None = None
            try:
                item = await self._scheduler.dequeue(timeout=1.0)
                if item is None:
                    continue

                run_id, (context, task_msg) = item
                active_context = context
                if my_task is not None:
                    self._worker_run_ids[my_task] = context.run_id

                # 把入站 traceparent 绑到本 Task 的 ContextVar，之后 logger /
                # report_result / send_log_batch 等出站点自动透传同一个 trace。
                # ``traceparent`` 是 transport 在 poll_task 后 setattr 上去的动态
                # 属性，TaskMessage dataclass 上没有这个字段。
                inbound_traceparent = getattr(task_msg, "traceparent", "") or ""
                if inbound_traceparent:
                    set_current_trace(child_span(inbound_traceparent).traceparent)
                else:
                    set_current_trace(new_trace().traceparent)

                result = await self._execute_or_resume_settlement(context, task_msg)
                await self._report_result(context, result)
                active_context = None

            except asyncio.CancelledError:
                # 只有 grace 超时被 hard cancel 才走到这里（正常缩容命中上面的
                # drain return）。必须兜底 kill + 上报：asyncio 的 cancel 只中断读
                # pipe 的协程、不 kill subprocess，留下的孤儿进程会继续产生外部副
                # 作用，master 看不到终态，PEL reclaim 后另一台 worker 再跑一遍。
                if active_context is not None:
                    await self._handle_forced_cancel(active_context, worker_id)
                break
            except Exception as exc:
                raise_if_generation_lost(exc)
                logger.exception(f"Worker-{worker_id} 异常")
            finally:
                if my_task is not None:
                    self._worker_run_ids.pop(my_task, None)

    async def _execute_or_resume_settlement(self, context: RunContext, task_msg: TaskMessage) -> ExecResult:
        """重投已有结果时只恢复结算，不再次进入 executor。"""
        if not await self._state_manager.has_pending_settlement(context.run_id):
            try:
                return await self._execute_task(context, task_msg)
            finally:
                self._record_task_completed(context.project_id)
        result, _, _ = await self._state_manager.settlement_snapshot(context.run_id)
        return result

    async def _handle_forced_cancel(self, context: RunContext, worker_id: int) -> None:
        """worker 被 hard cancel 时的清理路径，三件事缺一不可：

        子进程被 SIGTERM/SIGKILL（不再产生外部副作用）、master 侧看到 CANCELLED
        终态、PEL 消息被 ACK（不会被其它 worker reclaim 后再跑一遍）。
        """
        run_id = context.run_id
        logger.warning(f"Worker-{worker_id} 被强制取消, 清理在途任务: run_id={run_id}")
        await cancel_executor_run(self._executor, run_id, "worker forced cancellation")
        if self._ownership_fenced:
            logger.error("ownership 已丢失，跳过旧 owner 结果上报与 ACK: run_id={}", run_id)
            return
        relay_error = self._forced_cancel_relay_errors.pop(run_id, "")
        result = self._build_forced_cancel_result(run_id, relay_error)
        await self._report_result_by_info(
            run_id=run_id,
            task_id=context.task_id,
            receipt=context.receipt,
            result=result,
        )

    def _build_forced_cancel_result(self, run_id: str, relay_error: str) -> ExecResult:
        now = datetime.now()
        if not relay_error:
            return self._build_cancelled_result(
                run_id,
                now,
                reason="worker cancelled (shutdown/shrink)",
            )
        return ExecResult(
            run_id=run_id,
            status=RunStatus.FAILED,
            exit_reason=ExitReason.ERROR,
            error_message=f"强制取消期间 SpiderData relay 失败: {relay_error}",
            started_at=now,
            finished_at=now,
        )

    async def _execute_task(self, context: RunContext, task_msg: TaskMessage) -> ExecResult:
        run_id = context.run_id
        started_at = datetime.now()
        log_manager = None
        runtime_handle = None
        exec_plan: ExecPlan | None = None
        spool_relayed = False

        try:
            if not await self._state_manager.transition(run_id, RunState.PREPARING):
                raise RuntimeError(f"任务无法进入 PREPARING 状态: run_id={run_id}")

            if await self._is_cancel_requested(run_id):
                return self._build_cancelled_result(run_id, started_at, "任务已取消")

            # P1-17: 必须在起用户进程**之前**持久化 RUNNING。不上报则准备阶段崩溃后
            # master 只见 DISPATCHED + runtime_status=NULL，任务永卡 DISPATCHING 既不
            # 失败也不补派；RUNNING 未落库就起进程则会被 reconcile 判失败并补派双跑。
            await self._run_preparation_step(
                run_id,
                lambda: self._report_running_start(context, started_at),
            )

            payload = self._build_payload(task_msg)
            payload.run_id = run_id
            payload.project_id = context.project_id

            source_bundle = getattr(task_msg, "source_bundle", None)
            if source_bundle is not None and not self._project_fetcher:
                raise RuntimeError("source_bundle 任务必须配置 project_fetcher")
            if self._project_fetcher and source_bundle is not None:
                workspace = await self._run_preparation_step(
                    run_id,
                    lambda: self._project_fetcher.fetch(
                        run_id=run_id,
                        project_id=context.project_id,
                        source_bundle_uri=source_bundle.uri,
                        source_bundle_sha256=source_bundle.sha256,
                        source_bundle_size=source_bundle.size or 0,
                        entry_point=payload.entry_point,
                        source_subdir=getattr(source_bundle, "source_subdir", None)
                        or getattr(task_msg, "source_subdir", "")
                        or "",
                    ),
                )
                # 插件读的是 workspace_path / project_cwd 这两个字段。
                payload.workspace_path = workspace.bundle_root or ""
                payload.project_cwd = workspace.project_cwd or workspace.bundle_root or ""

            runtime_handle = await self._run_preparation_step(run_id, lambda: self._prepare_runtime(context))

            if await self._is_cancel_requested(run_id):
                return self._build_cancelled_result(run_id, started_at, "任务已取消")

            if self._plugin_registry:
                exec_plan = await self._run_preparation_step(
                    run_id,
                    lambda: self._plugin_registry.build_plan(context, payload),
                )
            else:
                exec_plan = self._build_fallback_plan(context, payload, runtime_handle)

            self._stamp_plan_scope(exec_plan, payload, run_id)

            self._apply_runtime_env(exec_plan, context)

            # 透传给子进程：用户脚本读 TRACEPARENT / ANTCODE_TRACE_ID 就能接上
            # Master → Worker → 子进程的同一条 trace。
            inbound_traceparent = getattr(task_msg, "traceparent", "") or ""
            if inbound_traceparent:
                exec_plan.env["TRACEPARENT"] = inbound_traceparent
                ids = parse_traceparent(inbound_traceparent)
                exec_plan.env["ANTCODE_TRACE_ID"] = ids.trace_id if ids else ""

            if await self._is_cancel_requested(run_id):
                return self._build_cancelled_result(run_id, started_at, "任务已取消")

            log_sink = None
            if self._log_manager_factory:
                log_manager = self._log_manager_factory.create(run_id)
                await self._run_preparation_step(run_id, log_manager.start)
                log_sink = log_manager

            # PREPARING -> RUNNING 与 cancel_requested 在同一把状态锁下判定。
            # 该转换紧邻 executor.run，避免取消看到 RUNNING 时执行器尚未进入
            # 可取消的 launch/marker 注册阶段。
            if not await self._state_manager.transition_if_not_cancel_requested(run_id, RunState.RUNNING):
                return self._build_cancelled_result(run_id, started_at, "任务已取消")

            with execution_egress(self, exec_plan) as execution_plan:
                exec_result = await execute_with_admission(
                    self,
                    execution_plan,
                    runtime_handle=runtime_handle,
                    log_sink=log_sink,
                )
            await self._relay_spider_spool(exec_plan, context)
            spool_relayed = True
            exec_result = await complete_execution(
                self,
                exec_plan,
                exec_result,
                context=context,
                runtime_handle=runtime_handle,
                log_manager=log_manager,
            )
            return exec_result

        except asyncio.CancelledError:
            if exec_plan is not None and not spool_relayed:
                await self._relay_on_forced_cancel(exec_plan, context)
            raise
        except Exception as error:
            return await handle_execution_failure(
                engine=self,
                run_id=run_id,
                started_at=started_at,
                error=error,
            )
        finally:
            # log_manager.stop 的异常必须隔离：finally 里抛出会替换 try 块的返回值，
            # 被外层 _worker_loop 吞掉，结果永不上报也不 ACK，master 侧卡在 running。
            if log_manager:
                try:
                    await log_manager.stop()
                except Exception as exc:
                    raise_if_generation_lost(exc)
                    logger.warning(f"log_manager.stop 失败但不影响结果上报: {exc}")
            if runtime_handle and self._runtime_manager:
                try:
                    await self._runtime_manager.release(runtime_handle)
                except Exception:
                    logger.exception(f"释放 runtime handle 失败: run_id={run_id}")
            # 清理 fetched workspace，避免无限堆积。
            if self._project_fetcher is not None:
                try:
                    await self._project_fetcher.cleanup(run_id)
                except Exception:
                    logger.exception(f"清理 workspace 失败: run_id={run_id}")
            # fetcher.cleanup 只清 workspace，管不到 rule/spider 的 tmp fallback 目录。
            await self._cleanup_rule_tmp(run_id)

    # W1: 二次取消时等 relay 收尾的有界时长（秒）。
    FORCED_CANCEL_RELAY_WAIT_SECONDS = 5.0

    async def _relay_on_forced_cancel(self, exec_plan: ExecPlan, context: RunContext) -> None:
        relay_task = asyncio.create_task(self._relay_spider_spool(exec_plan, context))
        try:
            await asyncio.shield(relay_task)
        except asyncio.CancelledError:
            # shield 期间再次被取消时，relay_task 会被留在后台继续读 spool，而上层
            # finally 的 _cleanup_rule_tmp 紧接着 rmtree 同一目录 —— 数据静默丢失。
            # 所以必须【确保 relay 真正退出】再返回；确保不了就跳过本 run 的 rmtree。
            # 不变式：_cleanup_rule_tmp 绝不与仍在读 spool 的 relay 并存。
            stopped = await self._stop_relay_bounded(relay_task)
            if not stopped:
                self._deferred_rule_tmp_cleanup.add(context.run_id)
                logger.error(
                    f"强制取消时 SpiderData relay 未能在 {self.FORCED_CANCEL_RELAY_WAIT_SECONDS}s 内退出，"
                    f"跳过 rule tmp 清理以避免与 relay 读 spool 竞态: run_id={context.run_id}"
                )
            self._forced_cancel_relay_errors[context.run_id] = "强制取消期间 SpiderData relay 被中断"
            logger.warning(f"强制取消时 SpiderData relay 被中断: run_id={context.run_id}")
            raise
        except Exception as exc:
            self._forced_cancel_relay_errors[context.run_id] = str(exc)
            logger.exception(f"强制取消时 SpiderData relay 失败: run_id={context.run_id}")

    async def _stop_relay_bounded(self, relay_task: asyncio.Task) -> bool:
        """有界地确保 relay_task 结束，返回是否已 done。

        即便本协程在收尾期间再次被取消，也重发取消并继续有界等待，绝不把 relay
        留在后台。到达硬 deadline 仍未 done 则返回 False，由调用方跳过 rmtree。
        """
        relay_task.cancel()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.FORCED_CANCEL_RELAY_WAIT_SECONDS
        while not relay_task.done():
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait({relay_task}, timeout=remaining)
            except asyncio.CancelledError:
                relay_task.cancel()
        if relay_task.done() and not relay_task.cancelled():
            with contextlib.suppress(Exception):
                relay_task.exception()
        return relay_task.done()

    @staticmethod
    def _apply_runtime_env(exec_plan: ExecPlan, context: RunContext) -> None:
        if not context.runtime_spec or not context.runtime_spec.env_vars:
            return
        from antcode_worker.transport.task_message_validation import validate_task_environment

        runtime_env = context.runtime_spec.env_vars
        if exec_plan.plugin_name in {"rule", "spider"}:
            reserved = RULE_PLUGIN_ENV_VARS.intersection(runtime_env)
            if reserved:
                names = ", ".join(sorted(reserved))
                raise RuntimeError(f"Spider runtime env 不得覆盖 Worker 控制变量: {names}")
        runtime_env = validate_task_environment(runtime_env)
        exec_plan.env.update(runtime_env)

    async def _relay_spider_spool(self, exec_plan: ExecPlan, context: RunContext) -> None:
        spool_path = exec_plan.env.get("ANTCODE_SPIDER_SPOOL_PATH", "")
        if not spool_path:
            if exec_plan.plugin_name in {"rule", "spider"}:
                raise RuntimeError("Spider 执行计划缺少 ANTCODE_SPIDER_SPOOL_PATH")
            return
        self._record_spider_stats(exec_plan.env)
        await relay_spider_spool(
            spool_path,
            self._transport,
            expected_run_id=context.run_id,
            expected_project_id=context.project_id,
        )

    async def _report_running_start(self, context: RunContext, started_at: datetime) -> None:
        """Worker 接单后持久化 RUNNING，失败时禁止启动任务。

        走 transport.report_result 把 status="running" 写到 task:result Stream；
        master 侧 update_result 据此把 dispatch_status → ACKED、runtime_status →
        RUNNING。这里不 ACK 也不 remove 本地 state，终态仍由 _report_result 上报。
        """
        from antcode_worker.transport.base import TaskResult

        running_result = TaskResult(
            run_id=context.run_id,
            task_id=context.task_id,
            status="running",
            error_message="",
            started_at=started_at,
            finished_at=None,
            duration_ms=0,
            data={
                "event": "worker_ack",
                "lease_id": self._resolve_lease_id(),
            },
        )
        if not await self._transport.report_result(running_result):
            raise RuntimeError(f"RUNNING 状态持久化失败: run_id={context.run_id}")

    async def _report_result(self, context: RunContext, result: ExecResult) -> None:
        """保存结果并幂等推进 result -> receipts ACK -> ownership release。"""
        await self._state_manager.start_settlement(
            context.run_id,
            task_id=context.task_id,
            receipt=context.receipt,
            result=result,
        )
        completed = False
        try:
            await self._report_pending_result(context, result)
            completed = await self._ack_pending_task_receipts(context.run_id)
        finally:
            if not completed:
                await self._state_manager.release_settlement(context.run_id)
        if completed:
            await self._release_settled_ownership(context.run_id)

    def _task_result(self, context: RunContext, result: ExecResult) -> TaskResult:
        from antcode_worker.transport.base import TaskResult

        return TaskResult(
            run_id=context.run_id,
            task_id=context.task_id,
            status=result.status.value,
            exit_code=result.exit_code,  # 准备阶段失败没有退出码；折成 0 会与"真的退出 0"同信号
            error_message=normalize_persisted_error_message(result.error_message) or "",
            started_at=result.started_at,
            finished_at=result.finished_at,
            duration_ms=result.duration_ms,
            data={
                "artifacts": [a.to_dict() for a in result.artifacts],
                "stdout_lines": result.stdout_lines,
                "stderr_lines": result.stderr_lines,
                "lease_id": self._resolve_lease_id(),
            },
        )

    async def _report_pending_result(self, context: RunContext, result: ExecResult) -> None:
        _, reported, _ = await self._state_manager.settlement_snapshot(context.run_id)
        if reported:
            return
        report_ok = await self._settle_with_retry(
            f"结果上报 run_id={context.run_id}",
            lambda: self._transport.report_result(self._task_result(context, result)),
        )
        if not report_ok:
            raise RuntimeError(f"结果上报失败: run_id={context.run_id}")
        await self._state_manager.mark_result_reported(context.run_id)
        logger.info(f"结果已上报: {context.run_id}")

    async def _ack_pending_task_receipts(self, run_id: str) -> bool:
        while True:
            _, _, receipts = await self._state_manager.settlement_snapshot(run_id)
            for receipt in receipts:
                acked = await self._settle_with_retry(
                    f"任务 ACK run_id={run_id}",
                    lambda receipt=receipt: self._transport.ack_task(receipt, accepted=True),
                )
                if not acked:
                    raise RuntimeError(f"任务 ACK 失败: run_id={run_id}")
                await self._state_manager.mark_receipt_acked(run_id, receipt)
            if await self._state_manager.finish_settlement(run_id):
                return True

    async def _release_settled_ownership(self, run_id: str) -> None:
        try:
            await self._release_run_ownership(run_id)
        except Exception as exc:
            raise_if_generation_lost(exc)
            logger.warning("release_run_ownership 失败: run_id={}, err={}", run_id, exc)

    # 结算重试参数：共 5 次尝试，失败后退避 1s/2s/4s/8s（最后一次不再等），重试窗口合计 15s。
    _SETTLE_MAX_ATTEMPTS = 5
    _SETTLE_BACKOFF_BASE_SECONDS = 1.0

    async def _settle_with_retry(self, op_name: str, operation) -> bool:
        """结算类出站调用的有界重试；最后一次的 False/异常原样返回/抛出。"""
        backoff = self._SETTLE_BACKOFF_BASE_SECONDS
        for attempt in range(1, self._SETTLE_MAX_ATTEMPTS + 1):
            try:
                if await operation():
                    return True
                failure: str | None = "returned False"
            except Exception as exc:
                raise_if_generation_lost(exc)
                if attempt >= self._SETTLE_MAX_ATTEMPTS:
                    raise
                failure = f"{type(exc).__name__}: {exc}"
            if attempt >= self._SETTLE_MAX_ATTEMPTS:
                return False
            logger.warning(f"{op_name} 失败（{failure}），{backoff:.0f}s 后重试 {attempt}/{self._SETTLE_MAX_ATTEMPTS}")
            await asyncio.sleep(backoff)
            backoff *= 2
        return False

    async def _cleanup_rule_tmp(self, run_id: str) -> None:
        """清理 `<tmp>/antcode-rule/{run_id}/` 兜底目录。

        路径约定与 ``rule/plugin.py::_resolve_rule_dir`` 的 fallback 分支、
        ``spider/plugin.py`` 一致。只删 run_id 子目录，绝不动上层
        `antcode-rule/` 根——并发 run 共享该父目录。
        """
        if not run_id:
            return
        if run_id in self._deferred_rule_tmp_cleanup:
            # W2: relay 未能有界退出，跳过本次 rmtree 以免与 relay 读 spool 竞态。
            # 残留目录交由后续 tmp 清理 / 系统 tmp 回收兜底。
            self._deferred_rule_tmp_cleanup.discard(run_id)
            logger.warning(f"跳过 rule tmp 清理（relay 未在时限内退出）: run_id={run_id}")
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
        return ExecResult(
            run_id=run_id,
            status=RunStatus.CANCELLED,
            exit_reason=ExitReason.CANCELLED,
            error_message=reason,
            started_at=started_at,
            finished_at=datetime.now(),
        )

    async def _is_cancel_requested(self, run_id: str) -> bool:
        info = await self._state_manager.get(run_id)
        if not info:
            return False
        return bool(info.data.get("cancel_requested"))

    async def _run_preparation_step(
        self,
        run_id: str,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run one preparation operation that cancel() can stop and await."""
        if await self._is_cancel_requested(run_id):
            raise PreparationCancelledError(run_id)
        return await self._preparation_tasks.run(run_id, operation)

    async def _settle_tombstoned_task(self, run_id: str, task_msg: Any) -> None:
        """tombstone 命中：按 CANCELLED 结算并 ACK，任务不进入本地队列。"""
        logger.info(f"任务到达前已被取消(tombstone 命中)，直接结算: run_id={run_id}")
        result = self._build_cancelled_result(run_id, datetime.now(), "cancelled before dispatch arrived")
        receipt = getattr(task_msg, "receipt", None)
        await self._report_result_by_info(run_id=run_id, task_id=task_msg.task_id, receipt=receipt, result=result)

    async def cancel(self, run_id: str, reason: str = "") -> bool:
        request = await self._state_manager.request_cancel(run_id)
        if request is None:
            # FN-01(c): run 尚未到达本地——记 tombstone 并放行 control ACK；
            # 任务消息随后到达时在 poll 准入被拦截。
            await self._cancel_tombstones.record(run_id, reason)
            return True

        if request.state in (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED):
            return False

        if request.state == RunState.QUEUED:
            await cancel_queued_run(self, request, reason)
            logger.info(f"任务已取消: {run_id}, reason={reason}")
            return True

        await cancel_started_run(self, request, reason)

        logger.info(f"任务已取消: {run_id}, reason={reason}")
        return True

    async def _drain_tasks(self) -> None:
        while True:
            count = await self._state_manager.count_active()
            if count == 0:
                break
            await asyncio.sleep(0.5)

    async def _force_terminate(self) -> None:
        await self.cancel_all(reason="force_terminate")

    def get_stats(self) -> dict:
        return {
            "running": self._running,
            "polling": self._polling,
            "queue_size": self._scheduler.size,
            "max_concurrent": self._max_concurrent,
        }

    def _generate_run_id(self, task_id: str) -> str:
        return f"run-{task_id}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    # 跨机 run 归属期（秒）= settings.TASK_EXECUTION_TIMEOUT(3600) + 冗余；
    # 正常 ack 或结果回传时会 DEL，Redis 侧不会长期占用。
    _RUN_OWNERSHIP_TTL_SECONDS = 3600 + 300
    # 配合 wakeup 事件把切代窗口压到 sub-second，别再调大。
    _RUN_OWNERSHIP_RENEW_INTERVAL_SECONDS = 60

    def _resolve_worker_id(self) -> str:
        """从 transport / config 里按下面的顺序解析 worker_id 并缓存。

        ``TransportBase`` 上没有公开的 ``worker_id`` 属性，所以要逐个模式试私有
        字段。解析不到必须 raise：静默兜底成 ``"unknown"`` 会让所有 ownership 键
        撞在同一个值上，跨机去重形同虚设。
        """
        if self._worker_id_cache:
            return self._worker_id_cache

        transport = self._transport
        candidates: list[Any] = []
        candidates.append(getattr(transport, "worker_id", None))
        # Direct 模式：RedisTransport._worker_id
        candidates.append(getattr(transport, "_worker_id", None))
        # Gateway 模式：GatewayTransport._gateway_config.worker_id
        gw_config = getattr(transport, "_gateway_config", None)
        if gw_config is not None:
            candidates.append(getattr(gw_config, "worker_id", None))
        # 通用 ServerConfig 兜底
        cfg = getattr(transport, "_config", None)
        if cfg is not None:
            candidates.append(getattr(cfg, "worker_id", None))

        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                resolved = candidate.strip()
                self._worker_id_cache = resolved
                return resolved

        raise RuntimeError(
            "engine 无法解析 worker_id: transport / config 都没有暴露有效 "
            "worker_id;请确认 wiring 层把 worker_id 写入了 transport(_worker_id 或 "
            "_gateway_config.worker_id)。不再静默用 'unknown' 兜底,避免跨机 fencing 失效。"
        )

    def _resolve_lease_id(self) -> str:
        lease_id = getattr(self._transport, "_lease_id", None)
        if not isinstance(lease_id, str) or not lease_id.strip():
            raise RuntimeError("engine 无法解析有效 lease_id，拒绝获取 run ownership")
        return lease_id.strip()

    async def _claim_run_ownership(self, run_id: str) -> bool:
        """Claim the run fence through the active transport boundary."""
        try:
            acquired = await self._transport.claim_run_ownership(
                run_id,
                self._RUN_OWNERSHIP_TTL_SECONDS * MILLISECONDS_PER_SECOND,
            )
        except Exception as exc:
            raise_if_generation_lost(exc)
            raise RuntimeError(f"ownership claim 失败: run_id={run_id}, error={exc}") from exc
        if acquired:
            self._released_ownership.forget(run_id)
        return acquired

    async def _release_run_ownership(self, run_id: str) -> None:
        """Release the run fence through the active transport boundary."""
        # 先登记再释放：登记晚于释放就会留出「key 已没、账本还没记」的窗口，
        # 恰好落在这个窗口的续租会被误判成 fence 被抢而杀掉整个进程。
        self._released_ownership.record_release(run_id)
        try:
            await self._transport.release_run_ownership(run_id)
        except Exception as exc:
            raise_if_generation_lost(exc)
            raise RuntimeError(f"ownership release 失败: run_id={run_id}") from exc

    async def _abort_for_ownership_failure(self, reason: str) -> None:
        await abort_for_ownership_failure(self, reason)

    async def _renew_run_ownership_loop(self) -> None:
        """Renew active run fences; P1-GW-02 wakeup 可提前触发一次 renew。"""
        if self._ownership_renew_wakeup is None:
            self._ownership_renew_wakeup = asyncio.Event()
        while self._running:
            try:
                try:
                    await asyncio.wait_for(
                        self._ownership_renew_wakeup.wait(),
                        timeout=self._RUN_OWNERSHIP_RENEW_INTERVAL_SECONDS,
                    )
                except TimeoutError:
                    pass
                else:
                    self._ownership_renew_wakeup.clear()
                if not self._running:
                    break
                await self._renew_active_run_ownership()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("ownership 续租循环异常")
                await self._abort_for_ownership_failure(str(exc))

    async def _on_transport_lease_revoked(self, reason: str = "lease-revoked") -> None:
        """transport 撤销后立即进入进程级 self-fence。"""
        logger.error("transport 报告 Lease 被撤销 (reason={}), 触发 self-fence", reason)
        self.request_ownership_renew_now()
        await self._abort_for_ownership_failure(f"lease-revoked: {reason}")

    def request_ownership_renew_now(self) -> None:
        """P1-GW-02: 请求立即 renew(loop 未启动时 no-op)。"""
        if self._ownership_renew_wakeup is not None:
            self._ownership_renew_wakeup.set()

    async def cancel_all(self, reason: str = "") -> int:
        """P1-GW-02: 取消所有 RUNNING/CANCELLING/PREPARING/QUEUED run,返回取消数。"""
        runs = await self._state_manager.get_all()
        cancellable = {RunState.RUNNING, RunState.CANCELLING, RunState.PREPARING, RunState.QUEUED}
        cancelled = 0
        failures: list[BaseException] = []
        for info in runs:
            if info.state not in cancellable:
                continue
            try:
                if await self.cancel(info.run_id, reason=reason or "engine.cancel_all"):
                    cancelled += 1
            except Exception as exc:
                logger.opt(exception=exc).error("cancel_all: cancel {} 失败", info.run_id)
                failures.append(exc)
        if failures:
            failed = ", ".join(str(error) for error in failures)
            raise CancellationError(f"cancel_all 未能终止全部任务: {failed}", reason=reason)
        return cancelled

    async def _renew_active_run_ownership(self) -> None:
        self._released_ownership.begin_renewal_pass()
        runs = await self._state_manager.get_all()
        active_states = {RunState.RUNNING, RunState.CANCELLING, RunState.PREPARING}
        for info in runs:
            if info.state not in active_states and info.settlement_result is None:
                continue
            await self._renew_one_run_ownership(info.run_id)

    async def _renew_one_run_ownership(self, run_id: str) -> None:
        try:
            renewed = await self._renew_run_ownership(run_id)
        except Exception as exc:
            raise RuntimeError(f"ownership 续租失败: run_id={run_id}") from exc
        if renewed:
            return
        # renew 的 False 混了两种意思（见 released_ownership 模块注释）。只有
        # 「我自己在本轮里放掉的」才放行；其余一律 fail-closed 自我围栏。
        if self._released_ownership.was_released_by_self(run_id):
            logger.debug("跳过已自行释放 run 的续租: run_id={}", run_id)
            return
        raise OwnershipFenceError(f"ownership 已丢失: run_id={run_id}")

    async def _renew_run_ownership(self, run_id: str) -> bool:
        ttl_ms = self._RUN_OWNERSHIP_TTL_SECONDS * MILLISECONDS_PER_SECOND
        return await self._transport.renew_run_ownership(run_id, ttl_ms)

    def _build_payload(self, task_msg: TaskMessage) -> Any:
        from antcode_worker.domain.enums import TaskType
        from antcode_worker.domain.models import TaskPayload

        project_type = getattr(task_msg, "project_type", "code") or "code"
        project_type = str(project_type).lower()
        task_type = {
            "spider": TaskType.SPIDER,
            "render": TaskType.RENDER,
            "code": TaskType.CODE,
            "file": TaskType.CODE,  # 文件项目复用 CODE 插件执行
            "rule": TaskType.RULE,
        }.get(project_type, TaskType.CUSTOM)

        source_bundle = getattr(task_msg, "source_bundle", None)
        if project_type != "rule" and source_bundle is None:
            raise ValueError("source_bundle 不能为空")

        params = getattr(task_msg, "params", {}) or {}
        args: list[str] = []
        kwargs: dict[str, Any] = {}
        artifact_patterns: list[str] = []
        if isinstance(params, dict):
            args = params.get("args", []) if isinstance(params.get("args", []), list) else []
            kwargs = params.get("kwargs", {}) if isinstance(params.get("kwargs", {}), dict) else params
            if isinstance(params.get("artifact_patterns"), list):
                artifact_patterns = params.get("artifact_patterns", [])
        elif isinstance(params, list):
            args = params

        from antcode_worker.transport.task_message_validation import validate_task_environment

        raw_env = getattr(task_msg, "environment", {}) or {}
        if not isinstance(raw_env, dict):
            raise TypeError("environment 必须是对象")
        env_vars = validate_task_environment(raw_env)

        return TaskPayload(
            task_type=task_type,
            source_bundle=source_bundle,
            entry_point=getattr(task_msg, "entry_point", "") or "",
            args=args,
            kwargs=kwargs,
            env_vars=env_vars,
            artifact_patterns=artifact_patterns,
        )

    async def apply_config_update(self, config: dict[str, Any]) -> None:
        current = CurrentCapacity(self._max_concurrent, self._policies.resource.memory_limit_mb)
        update = resolve_engine_config_update(config, self._adaptive_limits_provider, current)
        if update.max_concurrent_tasks is not None and update.max_concurrent_tasks != self._max_concurrent:
            await self._resize_workers(update.max_concurrent_tasks)
        if update.task_memory_limit_mb is not None:
            self._policies.resource.memory_limit_mb = update.task_memory_limit_mb
        if update.task_cpu_time_limit_sec is not None:
            self._policies.resource.cpu_limit_seconds = update.task_cpu_time_limit_sec
        if update.auto_resource_limit is not None:
            self._auto_resource_limit = update.auto_resource_limit

    # 缩容 drain 窗口（秒）：300s 大于常见短任务，又不至于让配置更新长期挂起。
    _SHRINK_DRAIN_GRACE_SECONDS = 300.0
    # hard cancel 前留给 self.cancel 走完 executor.cancel + 上报 CANCELLED 的窗口。
    _SHRINK_CANCEL_TIMEOUT_SECONDS = 30.0

    async def _resize_workers(self, new_max: int) -> None:
        """动态调整并发 worker 数量；缩容走 ``_shrink_workers`` 的三段式关闭。"""
        diff = new_max - self._max_concurrent
        if diff == 0:
            return

        await apply_capacity_limits(
            self._scheduler,
            self._executor,
            self._capacity_observer,
            previous=self._max_concurrent,
            target=new_max,
        )
        self._max_concurrent = new_max
        self._policies.resource.max_concurrent = new_max

        if not self._running:
            return

        if diff > 0:
            for _ in range(diff):
                self._worker_tasks.append(self._create_worker_task())
        else:
            self._schedule_worker_shrink(-diff)

    def _create_worker_task(self) -> asyncio.Task:
        worker_id = self._next_worker_id
        self._next_worker_id += 1
        operation = run_with_generation_fence(self, partial(self._worker_loop, worker_id))
        return asyncio.create_task(operation)

    def _schedule_worker_shrink(self, drain_count: int) -> None:
        draining = self._take_workers_for_drain(drain_count)
        if not draining:
            return
        task = asyncio.create_task(self._shrink_workers(draining))
        self._worker_shrink_tasks.add(task)
        task.add_done_callback(self._worker_shrink_done)

    def _take_workers_for_drain(self, drain_count: int) -> list[asyncio.Task]:
        draining: list[asyncio.Task] = []
        for _ in range(max(0, drain_count)):
            if not self._worker_tasks:
                break
            task = self._worker_tasks.pop()
            self._draining_worker_tasks.add(task)
            draining.append(task)
        return draining

    def _worker_shrink_done(self, task: asyncio.Task) -> None:
        self._worker_shrink_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        logger.opt(exception=error).critical("后台 worker 缩容失败")
        self._fatal_error_signal.record(RuntimeError(f"后台 worker 缩容失败: {error}"))

    async def _shrink_workers(self, draining: list[asyncio.Task]) -> None:
        """三段式优雅缩容。**不能简化成直接 task.cancel()**：asyncio 只 cancel 读
        pipe 的协程、不 kill subprocess，孤儿子进程会继续写库/发消息却不再上报，
        master 停在 DISPATCHED，PEL reclaim 后另一台 worker 再跑一遍同 run_id。

        阶段 1 (drain):  塞进 _draining_worker_tasks，worker 在下一轮起点自然退出，
                         在途任务跑完 + 正常 ACK + 上报终态。
        阶段 2 (cancel): 超过 grace period 走 self.cancel(run_id) 触发 executor 的
                         SIGTERM + grace + SIGKILL，_execute_task 拿到 CANCELLED
                         结果后正常上报终态 + ACK PEL。
        阶段 3 (force):  还挂着才 task.cancel()，由 _worker_loop 的 CancelledError
                         分支兜底 kill + 上报。
        """
        if not draining:
            return
        logger.info(f"P1-26: 优雅缩容 {len(draining)} 个 worker (drain 阶段开始)")

        _, pending = await asyncio.wait(draining, timeout=self._SHRINK_DRAIN_GRACE_SECONDS)
        if not pending:
            for task in draining:
                self._draining_worker_tasks.discard(task)
            logger.info("P1-26: 缩容完成(drain 阶段全部退出)")
            return

        logger.warning(
            f"P1-26: {len(pending)} 个 worker drain 超时, 进入 cancel 阶段 (grace={self._SHRINK_DRAIN_GRACE_SECONDS}s)"
        )
        # 只取消归属于待退出 worker task 的 run，不能误伤保留 worker 上的在途任务。
        failures = await self._cancel_draining_runs(pending)

        _, still_pending = await asyncio.wait(pending, timeout=self._SHRINK_CANCEL_TIMEOUT_SECONDS)
        if still_pending:
            logger.warning(f"P1-26: {len(still_pending)} 个 worker cancel 超时, 强制 cancel")
            for task in still_pending:
                task.cancel()
            results = await asyncio.gather(*still_pending, return_exceptions=True)
            failures.extend(result for result in results if isinstance(result, Exception))
        for task in draining:
            self._draining_worker_tasks.discard(task)
        if failures:
            raise ExceptionGroup("worker 缩容取消失败", failures)
        logger.info("P1-26: 缩容完成")

    async def _cancel_draining_runs(self, pending: set[asyncio.Task]) -> list[Exception]:
        failures: list[Exception] = []
        for task in pending:
            run_id = self._worker_run_ids.get(task)
            if not run_id:
                continue
            try:
                await self.cancel(run_id, reason="worker shrink")
            except Exception as exc:
                logger.opt(exception=exc).error("P1-26: shrink cancel 失败: run_id={}", run_id)
                failures.append(exc)
        return failures

    async def _prepare_runtime(self, context: RunContext) -> Any:
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

        if not self._runtime_manager:
            raise RuntimeError("runtime_manager 或 runtime_spec 未配置")

        from antcode_worker.runtime.spec import LockSource, PythonSpec
        from antcode_worker.runtime.spec import RuntimeSpec as RuntimeSpecV2

        domain_spec = context.runtime_spec
        if domain_spec is None:
            from antcode_worker.engine.unbound_runtime import worker_python_runtime_handle

            return worker_python_runtime_handle()
        spec = RuntimeSpecV2(
            python_spec=PythonSpec(version=domain_spec.python_version, path=domain_spec.python_path),
            lock_source=LockSource(requirements=list(domain_spec.requirements)),
            constraints=list(domain_spec.constraints),
            extras=list(domain_spec.extras),
            env_vars=dict(domain_spec.env_vars),
        )
        return await self._runtime_manager.prepare(spec)

    @staticmethod
    def _stamp_plan_scope(exec_plan: ExecPlan, payload: Any, run_id: str) -> None:
        """把 run 归属与 source bundle 解包根目录钉到插件产出的执行计划上。"""
        # include_paths 共享目录落在 bundle 根下、cwd 的兄弟位置，沙箱要挂载它必须知道解包根。
        exec_plan.run_id = run_id
        exec_plan.workspace_root = payload.workspace_path or None

    def _build_fallback_plan(self, context: RunContext, payload: Any, runtime_handle: Any) -> Any:
        from antcode_worker.domain.models import ExecPlan

        command = runtime_handle.python_executable
        args = [payload.entry_point] if payload.entry_point else []
        work_dir = payload.project_cwd or payload.workspace_path
        if not work_dir:
            raise ValueError("workspace_path 不能为空")

        return ExecPlan(
            command=command,
            args=args,
            env=payload.env_vars,
            cwd=work_dir,
            timeout_seconds=context.timeout_seconds,
            memory_limit_mb=context.memory_limit_mb,
            cpu_limit_seconds=context.cpu_limit_seconds,
        )
