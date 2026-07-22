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
from dataclasses import dataclass
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
from antcode_worker.domain.models import ExecPlan, ExecResult, RunContext
from antcode_worker.engine.cancel_tombstones import CancelTombstones
from antcode_worker.engine.policies import Policies, default_policies
from antcode_worker.engine.rule_egress import rule_egress_plan
from antcode_worker.engine.runtime_control_guard import _require_live_runtime_control
from antcode_worker.engine.scheduler import Scheduler
from antcode_worker.engine.spider_spool import relay_spider_spool
from antcode_worker.engine.state import RunState, StateManager
from antcode_worker.executor.rule_policy import RULE_PLUGIN_ENV_VARS
from antcode_worker.transport.base import (
    ControlMessage,
    TaskMessage,
    TransportBase,
    TransportMode,
)

if TYPE_CHECKING:
    # 仅类型注解使用的依赖：避免运行时 import 引入循环 / 重负载。
    # 这些都是可选依赖，构造时以 ``None`` 兜底。
    from antcode_worker.executor.base import BaseExecutor
    from antcode_worker.runtime.manager import RuntimeManager


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
        self._cancel_tombstones = CancelTombstones()  # FN-01(c)

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
        self._runtime_control_tasks: dict[str, asyncio.Task] = {}
        self._runtime_control_receipts: dict[asyncio.Task, str] = {}

        # 资源限制
        self._policies.resource.max_concurrent = max_concurrent
        self._policies.resource.memory_limit_mb = memory_limit_mb
        self._policies.resource.cpu_limit_seconds = cpu_limit_seconds

        # P1-15: worker_id 缓存与续租后台任务。首次调用 _resolve_worker_id
        # 时从 transport / config 里解析并缓存;若最终解析不到就 raise,
        # 不再静默用 "unknown"。
        self._worker_id_cache: str | None = None
        self._ownership_renewal_task: asyncio.Task | None = None
        self._ownership_renew_wakeup: asyncio.Event | None = None  # P1-GW-02

        # P1-26: 优雅缩容用的 drain 集合。_resize_workers 缩容时不直接
        # cancel worker task(会让 ProcessExecutor 的子进程孤儿化, master 侧
        # 任务永卡 DISPATCHING / PEL reclaim 后另一台 worker 双跑), 而是把
        # 要退的 worker task 塞进这里, worker 在下一轮起点自然退出。
        # 超过 grace period 才走 self.cancel(kill 子进程 + 上报 CANCELLED),
        # 最后才 hard cancel 兜底。
        self._draining_worker_tasks: set[asyncio.Task] = set()
        self._worker_run_ids: dict[asyncio.Task, str] = {}
        self._forced_cancel_relay_errors: dict[str, str] = {}
        # W2: relay 未能在硬性时限内退出的 run，跳过其 rule tmp rmtree，
        # 避免 _cleanup_rule_tmp 与仍在读 spool 的 relay 竞态。
        self._deferred_rule_tmp_cleanup: set[str] = set()

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

        # P1-15: 后台续租跨机归属键,避免长跑任务 TTL 到期被重投另一台
        self._ownership_renewal_task = asyncio.create_task(self._renew_run_ownership_loop())

        # P1-GW-02: transport revoke → engine 立即 cancel_all + 唤醒 renew loop
        register = getattr(self._transport, "set_lease_revoked_callback", None)
        if callable(register):
            register(self._on_transport_lease_revoked)

        # P1-15: 启动时尝试解析 worker_id, 让配置错误在拉到第一条任务前
        # 就暴露出来。测试里 transport 是 MagicMock,resolve 会命中 fallback
        # RuntimeError, 这里只 warn 不 raise, 让单测的 start/stop 用例仍可
        # 通过; 真正跑到 _claim_run_ownership 时会 raise 拒绝静默继续。
        try:
            resolved = self._resolve_worker_id()
            logger.debug(f"engine 已解析 worker_id: {resolved}")
        except RuntimeError as exc:
            logger.warning(f"engine 启动时无法解析 worker_id, 首个任务到达时将 raise: {exc}")

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
        # P1-15: 停掉后台续租循环,避免关机中还在写归属键
        if self._ownership_renewal_task:
            self._ownership_renewal_task.cancel()

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

        await self._drain_runtime_controls(grace_period)

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
                    flow_acquired = await self._flow_controller.acquire(timeout=self._policies.timeout.poll_timeout)
                    if not flow_acquired:
                        await asyncio.sleep(0.1)
                        continue

                # 拉取任务
                task_msg = await self._transport.poll_task(timeout=self._policies.timeout.poll_timeout)
                if self._flow_controller:
                    self._flow_controller.on_success()

                if task_msg is None:
                    continue

                # 创建运行上下文
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
            except Exception as exc:
                from antcode_worker.transport.base import GenerationLostError

                if isinstance(exc, GenerationLostError):
                    # 复审 D2: 代际已被取代 —— 立即 abort 全部执行，
                    # 把旧代际僵尸进程的双执行窗口从 ownership 续租
                    # tick(~600s) 压缩到单次 poll。
                    logger.error("Lease generation 已被新代际取代，立即中止本进程全部执行")
                    await self._abort_for_ownership_failure("lease generation superseded")
                    break
                if self._flow_controller:
                    self._flow_controller.on_failure()
                logger.exception("轮询异常")
                await asyncio.sleep(1)
            finally:
                if self._flow_controller and flow_acquired:
                    await self._flow_controller.release()

    async def _admit_polled_task(self, run_id: str, task_msg: Any) -> bool:
        """poll 准入：tombstone → 去重 → ownership fence；False=已按各自语义处置。"""
        # FN-01(c): 取消先于任务到达时 cancel() 记了 tombstone；命中即按
        # CANCELLED 结算并 ACK，任务绝不执行。
        if self._cancel_tombstones.consume(run_id):
            await self._settle_tombstoned_task(run_id, task_msg)
            return False
        # B2: 本地去重——同一 PEL 消息重投时保持 pending，由原执行路径
        # 按 receipt 完成 XACK。
        _, is_new_local = await self._state_manager.add_if_new(run_id, task_msg.task_id, receipt=task_msg.receipt)
        if not is_new_local:
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
            # 从本地状态清理（我们没真的接手）
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
        """控制通道轮询（取消/kill）"""
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
            except Exception:
                logger.exception("控制通道异常")
                await asyncio.sleep(1)

    async def _dispatch_control(self, control: ControlMessage) -> None:
        """P0-03a: 从 _control_loop 拆出的分派 + ACK,C901 12 → 11。"""
        if control.control_type in ("cancel", "kill"):
            await self._invoke_cancel_control(control)
        elif control.control_type == "config_update":
            await self.apply_config_update(control.payload or {})
        elif control.control_type == "runtime_manage":
            self._schedule_runtime_control(control)
            return
        if control.receipt:
            await self._transport.ack_control(control.receipt)

    async def _invoke_cancel_control(self, control: ControlMessage) -> None:
        """P1-FN-04: 缺 target 只 log; cancel False 仍 ACK; 异常跳外层不 ACK。"""
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
        task = asyncio.create_task(self._handle_runtime_control(control))
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
        - Gateway 结果通过 ``AckControl`` 携带结构化数据回报；Gateway 从
          原始受认证事件派生 reply stream，Worker 不直接指定 Redis key

        P2-#32: 路由从 14 elif 链改为 ``_ACTION_HANDLERS`` 表驱动。
        """
        payload = control.payload or {}
        action = payload.get("action", "")
        request_id = payload.get("request_id", "")
        if not control.receipt:
            # 无 receipt 的事件没有 settlement 通路，只能 fail-fast
            # （_schedule_runtime_control 已前置拦截，这里是兜底防御）。
            raise RuntimeError("运行时控制事件缺少 receipt")
        if not request_id:
            # W4: 有 receipt 但缺 request_id 的畸形事件必须就地 ACK 丢弃。
            # 之前直接 raise 会让事件永不 settlement —— Direct 模式被
            # PendingControlRecovery、Gateway 模式被 WatchControl PEL 重放
            # 无限重投同一条坏消息。ACK 失败也不 raise（best-effort），
            # 重投后会再次走到这里重试 ACK。
            logger.warning(
                "丢弃缺少 request_id 的运行时控制事件: action={} receipt={}",
                action,
                control.receipt,
            )
            try:
                await self._transport.ack_control(control.receipt)
            except Exception:
                logger.exception("畸形运行时控制事件 ACK 失败，等待重投后重试")
            return
        # 新协议优先用 typed args；旧路径 fallback 到 payload 嵌套 dict
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
            success = False
            error_message = str(e)
            # P2: ``except Exception`` 块统一用 logger.exception 保留堆栈，
            # 避免错误被静默吞掉只剩 ``str(e)``。
            logger.exception(f"runtime action 失败: action={action} req={request_id}")

        # CancelledError 会在到达这里前向上传播，事件保持未 settlement。
        reply_stream = payload.get("reply_stream", "") or payload.get("params", {}).get("reply_stream", "")
        result = _RuntimeControlResult(
            request_id=request_id,
            reply_stream=reply_stream,
            success=success,
            receipt=control.receipt,
            data=result_data if success else None,
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
        """工作协程"""
        logger.debug(f"Worker-{worker_id} 启动")
        my_task = asyncio.current_task()

        while self._running:
            # P1-26: 缩容优雅退出 —— 被 _resize_workers 塞进 drain 集合的
            # worker 会在下一轮起点自然退出,让在途任务先跑完再退。
            if my_task is not None and my_task in self._draining_worker_tasks:
                self._draining_worker_tasks.discard(my_task)
                logger.info(f"Worker-{worker_id} drain 完成, 优雅退出")
                return

            # active_context 在 try 外面声明, 让 except CancelledError 分支
            # 也能拿到 —— 强制 cancel 时需要它触发 executor.cancel + 上报
            # CANCELLED 终态, 否则子进程孤儿化 + master 侧永卡 DISPATCHING。
            active_context: RunContext | None = None
            try:
                # 从队列取任务
                item = await self._scheduler.dequeue(timeout=1.0)
                if item is None:
                    continue

                run_id, (context, task_msg) = item
                active_context = context
                if my_task is not None:
                    self._worker_run_ids[my_task] = context.run_id

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
                active_context = None

            except asyncio.CancelledError:
                # P1-26: 强制取消分支 —— 直接 task.cancel() 会让 ProcessExecutor
                # 的子进程孤儿化(asyncio 只 cancel 读 pipe 的协程, 不 kill
                # subprocess),master 侧看不到终态、PEL reclaim 后新 worker
                # 又跑一遍,产生外部副作用双写。这里兜底:
                # 1) executor.cancel(run_id) 触发 _do_cancel → _terminate_process,
                #    SIGTERM + grace + SIGKILL 保证子进程真的死。
                # 2) 走 report_result 上报 status=CANCELLED,让 master 走 CAS
                #    终态吸收态,PEL 也被 ACK 掉,不会再被 reclaim。
                # 正常场景(shrink drain flag 命中)走上面的 return 分支,不会
                # 进这里;只有 grace period 超时被 hard cancel 时才走这里。
                if active_context is not None:
                    await self._handle_forced_cancel(active_context, worker_id)
                break
            except Exception:
                logger.exception(f"Worker-{worker_id} 异常")
            finally:
                if my_task is not None:
                    self._worker_run_ids.pop(my_task, None)

    async def _handle_forced_cancel(self, context: RunContext, worker_id: int) -> None:
        """P1-26: worker 被 hard cancel 时的清理路径。

        走这里意味着 ``_worker_loop`` 已经被 ``task.cancel()`` 中断,
        必须尽力做到:
        - 子进程被 SIGTERM/SIGKILL,不再产生外部副作用
        - master 侧看到 CANCELLED 终态,dispatch/runtime 都进终态吸收态
        - PEL 消息被 ACK,不会被其它 worker reclaim 后再跑一遍
        """
        run_id = context.run_id
        logger.warning(f"Worker-{worker_id} 被强制取消, 清理在途任务: run_id={run_id}")
        # 1) kill 子进程
        try:
            if self._executor is not None:
                await self._executor.cancel(run_id)
        except Exception:
            logger.exception(f"强制取消时 executor.cancel 失败: run_id={run_id}")
        # 2) 上报 CANCELLED 终态 + ACK PEL
        try:
            relay_error = self._forced_cancel_relay_errors.pop(run_id, "")
            result = self._build_forced_cancel_result(run_id, relay_error)
            await self._report_result_by_info(
                run_id=run_id,
                task_id=context.task_id,
                receipt=context.receipt,
                result=result,
            )
        except Exception:
            logger.exception(f"强制取消时上报终态失败: run_id={run_id}")

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
        """执行单个任务"""
        run_id = context.run_id
        started_at = datetime.now()
        log_manager = None
        runtime_handle = None
        exec_plan: ExecPlan | None = None
        spool_relayed = False

        try:
            # 转换状态
            await self._state_manager.transition(run_id, RunState.PREPARING)

            # P1-17: worker 收到任务后必须主动上报一次 status=RUNNING。
            # 底层复用 transport.report_result 的 task:result Stream 通道,
            # master.result_loop → task_run_service.update_result 会把
            # dispatch_status → ACKED, runtime_status → RUNNING(见
            # STATUS_MAPPING["running"]),同时终态保护不会翻转已终态记录。
            #
            # 不上报的话:worker 在准备 runtime / 下载 bundle / 起子进程
            # 之间任一阶段崩溃,master 侧永远只看到 dispatch_status=DISPATCHED
            # + runtime_status=NULL,任务永卡在 DISPATCHING 不失败也不补派。
            # RUNNING 未持久化时不得启动用户进程，否则 reconcile 可能把仍在
            # 执行的任务判失败并补派，形成双跑。
            await self._report_running_start(context, started_at)

            # 生成任务 payload
            payload = self._build_payload(task_msg)
            payload.run_id = run_id
            payload.project_id = context.project_id

            # V1/V1.5/V2: 下载/缓存项目 source bundle
            source_bundle = getattr(task_msg, "source_bundle", None)
            if source_bundle is not None and not self._project_fetcher:
                raise RuntimeError("source_bundle 任务必须配置 project_fetcher")
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
            self._apply_runtime_env(exec_plan, context)

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
            with rule_egress_plan(exec_plan) as execution_plan:
                exec_result = await self._executor.run(
                    execution_plan,
                    runtime_handle,
                    log_sink=log_sink,
                )

            await self._relay_rule_spool(exec_plan, context)
            spool_relayed = True

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

            # 日志已由 LogManager 流式持久化到 PostgreSQL，执行结束前仅需 flush。
            if log_manager:
                await log_manager.flush()

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

        except asyncio.CancelledError:
            if exec_plan is not None and not spool_relayed:
                await self._relay_on_forced_cancel(exec_plan, context)
            raise
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

    # W1: 二次取消时等 relay 收尾的有界时长（秒）。
    FORCED_CANCEL_RELAY_WAIT_SECONDS = 5.0

    async def _relay_on_forced_cancel(self, exec_plan: ExecPlan, context: RunContext) -> None:
        relay_task = asyncio.create_task(self._relay_rule_spool(exec_plan, context))
        try:
            await asyncio.shield(relay_task)
        except asyncio.CancelledError:
            # W2: shield 期间再次被取消（如 event loop 关停批量 cancel）时，
            # relay_task 会被 shield 留在后台继续读 spool 文件，而上层
            # finally 的 _cleanup_rule_tmp 紧接着 rmtree spool 目录，二者
            # 竞态会造成数据静默丢失 / 读已删文件。必须先中断 relay 并【确保
            # 它真正退出】再返回。旧实现只做一次 asyncio.wait(timeout=5)，超时
            # 后 relay 仍 pending 就直接 raise，等于把 relay 留给 rmtree 竞态。
            # 这里有界地确保 relay 结束；若有界时长内仍不退出，则跳过本 run 的
            # rmtree（不变式：_cleanup_rule_tmp 绝不与仍在读 spool 的 relay 竞态）。
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
        """有界地确保 relay_task 结束，返回是否已结束（done）。

        保证调用方后续的 _cleanup_rule_tmp(rmtree) 不与仍在读 spool 的 relay
        竞态：即便本协程在收尾期间再次被取消，也重发取消并继续有界等待，
        绝不把 relay 留在后台。到达硬性 deadline 仍未 done 时返回 False，由
        调用方决定跳过 rmtree。
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
                # 收尾期间本协程又被取消：重发取消并继续有界等待。
                relay_task.cancel()
        if relay_task.done() and not relay_task.cancelled():
            # 消费 relay 的终态异常，避免 "exception was never retrieved"。
            with contextlib.suppress(Exception):
                relay_task.exception()
        return relay_task.done()

    @staticmethod
    def _apply_runtime_env(exec_plan: ExecPlan, context: RunContext) -> None:
        if not context.runtime_spec or not context.runtime_spec.env_vars:
            return
        runtime_env = context.runtime_spec.env_vars
        if exec_plan.plugin_name == "rule":
            reserved = RULE_PLUGIN_ENV_VARS.intersection(runtime_env)
            if reserved:
                names = ", ".join(sorted(reserved))
                raise RuntimeError(f"Rule runtime env 不得覆盖 Worker 控制变量: {names}")
        exec_plan.env.update(runtime_env)

    async def _relay_rule_spool(self, exec_plan: ExecPlan, context: RunContext) -> None:
        if exec_plan.plugin_name != "rule":
            return
        spool_path = exec_plan.env.get("ANTCODE_SPIDER_SPOOL_PATH", "")
        if not spool_path:
            raise RuntimeError("Rule 执行计划缺少 ANTCODE_SPIDER_SPOOL_PATH")
        await relay_spider_spool(
            spool_path,
            self._transport,
            expected_run_id=context.run_id,
            expected_project_id=context.project_id,
        )

    async def _report_running_start(self, context: RunContext, started_at: datetime) -> None:
        """Worker 接单后持久化 RUNNING，失败时禁止启动任务。

        通道:走 transport.report_result 把 status="running" 写到 task:result
        Stream, master 的 result_loop → task_run_service.update_result 会:
        - dispatch_status → ACKED (rank 3 覆盖 DISPATCHED rank 2)
        - runtime_status  → RUNNING (从 NULL 进入 rank 2)

        不 ACK 也不 remove 本地 state；最终终态仍由 _report_result 上报。
        """
        from antcode_worker.transport.base import TaskResult

        running_result = TaskResult(
            run_id=context.run_id,
            task_id=context.task_id,
            status="running",
            exit_code=0,
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
        """上报结果（幂等）; P1-GW-05: try/finally 保证任何路径都释放 ownership。"""
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
                "stdout_lines": result.stdout_lines,
                "stderr_lines": result.stderr_lines,
                "lease_id": self._resolve_lease_id(),
            },
        )

        try:
            # 复审 D1: 结算带有界重试;瞬时失败不把 receipt 变成永久孤儿
            report_ok = await self._settle_with_retry(
                f"结果上报 run_id={context.run_id}",
                lambda: self._transport.report_result(task_result),
            )
            if report_ok:
                logger.info(f"结果已上报: {context.run_id}")
                # V12: 只有 report 成功才 ack,失败靠 PEL XAUTOCLAIM 让其它 worker 重试
                if context.receipt:
                    ack_ok = await self._settle_with_retry(
                        f"任务 ACK run_id={context.run_id}",
                        lambda: self._transport.ack_task(context.receipt, accepted=True),
                    )
                    if not ack_ok:
                        raise RuntimeError(f"任务 ACK 失败: run_id={context.run_id}")
                await self._state_manager.remove(context.run_id)
            else:
                raise RuntimeError(f"结果上报失败: run_id={context.run_id}")
        finally:
            # P1-GW-05: 任何路径都释放 ownership; release 失败只 warn 不覆盖原异常
            try:
                await self._release_run_ownership(context.run_id)
            except Exception as exc:
                logger.warning("release_run_ownership 失败: run_id={}, err={}", context.run_id, exc)

    # 结算重试参数：指数退避 1s→16s，共 5 次尝试（总窗口 ~31s）。
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
        """R5-P2-6: rule plugin fallback 路径 `/tmp/antcode-rule/{run_id}/` 兜底清理。

        与 rule/plugin.py::_resolve_rule_dir 的兜底约定一致。只删 run_id
        子目录，不 rm -rf 上层 `/tmp/antcode-rule/`（并发 run 会共享该父
        目录）。用 to_thread 避免阻塞 event loop。
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

    async def _settle_tombstoned_task(self, run_id: str, task_msg: Any) -> None:
        """tombstone 命中：按 CANCELLED 结算并 ACK，任务不进入本地队列。"""
        logger.info(f"任务到达前已被取消(tombstone 命中)，直接结算: run_id={run_id}")
        result = self._build_cancelled_result(run_id, datetime.now(), "cancelled before dispatch arrived")
        receipt = getattr(task_msg, "receipt", None)
        await self._report_result_by_info(run_id=run_id, task_id=task_msg.task_id, receipt=receipt, result=result)

    async def cancel(self, run_id: str, reason: str = "") -> bool:
        """取消任务"""
        info = await self._state_manager.get(run_id)
        if not info:
            # FN-01(c): run 尚未到达本地——记 tombstone 并放行 control ACK；
            # 任务消息随后到达时在 poll 准入被拦截。
            self._cancel_tombstones.record(run_id, reason)
            return True

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
                # P1-FN-04: False = executor 内已无 run(可能自然结束),继续走结算但 warn
                executor_cancelled = await self._executor.cancel(run_id)
                if not executor_cancelled:
                    logger.warning("executor.cancel 未找到 run: run_id={}", run_id)
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
    # P1-GW-02: 600s → 60s + wakeup 事件,把切代窗口从 10 分钟压到 sub-second
    _RUN_OWNERSHIP_RENEW_INTERVAL_SECONDS = 60

    def _resolve_worker_id(self) -> str:
        """P1-15: 从 transport / config 里解析 worker_id 并缓存。

        原实现直接读 ``self._transport.worker_id``——TransportBase 上并没有
        这个属性,所有 ownership 值退化为 ``"unknown"``,固定 TTL 又不续,
        Redis 异常还静默放行,等于跨机去重完全失效。

        新实现按顺序尝试:

        1. ``transport.worker_id`` (未来若在基类上补齐 property)
        2. Redis 直连 transport 的私有 ``_worker_id``
        3. Gateway transport 的 ``_gateway_config.worker_id``
        4. ``transport._config.worker_id`` (兜底)

        全部为空则 raise ``RuntimeError``——engine 启动就失败,不再静默
        用 "unknown" 让跨机去重形同虚设。
        """
        if self._worker_id_cache:
            return self._worker_id_cache

        transport = self._transport
        candidates: list[Any] = []
        # 1. 公开属性(未来可能在 TransportBase 上补齐)
        candidates.append(getattr(transport, "worker_id", None))
        # 2. Redis 直连模式: RedisTransport._worker_id
        candidates.append(getattr(transport, "_worker_id", None))
        # 3. Gateway 模式: GatewayTransport._gateway_config.worker_id
        gw_config = getattr(transport, "_gateway_config", None)
        if gw_config is not None:
            candidates.append(getattr(gw_config, "worker_id", None))
        # 4. 通用 ServerConfig fallback
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
        if self._uses_gateway_ownership_rpc():
            try:
                return await self._transport.claim_run_ownership(
                    run_id,
                    self._RUN_OWNERSHIP_TTL_SECONDS * MILLISECONDS_PER_SECOND,
                )
            except Exception as exc:
                raise RuntimeError(f"Gateway ownership claim 失败: run_id={run_id}") from exc
        return await self._claim_direct_run_ownership(run_id)

    def _direct_ownership_context(self) -> tuple[Any, str]:
        """Direct 模式 ownership 只用 transport 注入的 ACL client 和 namespace。

        P1-DR-01: 此前重新调用全局 ``get_redis_client()``/``redis_namespace()``，
        绕开 RedisTransport 按 Worker ACL 注入的 client；自定义
        ``WORKER_REDIS_NAMESPACE`` 时 key 串线，只配 ``WORKER_REDIS_URL`` 时
        claim 失败。找不到 client/namespace 直接拒绝，不做静默回退。
        """
        client = getattr(self._transport, "ownership_redis_client", None)
        namespace = getattr(self._transport, "ownership_redis_namespace", None)
        if client is None or not namespace:
            raise RuntimeError("Direct transport 未暴露 ownership Redis client/namespace，拒绝 ownership 操作")
        return client, str(namespace)

    async def _claim_direct_run_ownership(self, run_id: str) -> bool:
        from antcode_core.application.services.workers.run_ownership_fence import (
            OwnershipOutcome,
            claim_run_ownership,
        )

        redis, namespace = self._direct_ownership_context()
        try:
            outcome = await claim_run_ownership(
                redis,
                worker_id=self._resolve_worker_id(),
                lease_id=self._resolve_lease_id(),
                run_id=run_id,
                ttl_ms=self._RUN_OWNERSHIP_TTL_SECONDS * MILLISECONDS_PER_SECOND,
                namespace=namespace,
            )
        except Exception as exc:
            raise RuntimeError(f"跨机 ownership claim 失败: run_id={run_id}") from exc
        if outcome is OwnershipOutcome.LEASE_STALE:
            # 权威 Lease Hash 已换代：本进程不得再接手任何任务。
            raise RuntimeError(f"ownership claim 被拒: lease 已被新代际取代 run_id={run_id}")
        return outcome is OwnershipOutcome.ACQUIRED

    async def _release_run_ownership(self, run_id: str) -> None:
        """Release the run fence through the active transport boundary."""
        if self._uses_gateway_ownership_rpc():
            try:
                await self._transport.release_run_ownership(run_id)
                return
            except Exception as exc:
                raise RuntimeError(f"Gateway ownership release 失败: run_id={run_id}") from exc
        await self._release_direct_run_ownership(run_id)

    async def _release_direct_run_ownership(self, run_id: str) -> None:
        from antcode_core.application.services.workers.run_ownership_fence import release_run_ownership

        redis, namespace = self._direct_ownership_context()
        try:
            await release_run_ownership(
                redis,
                worker_id=self._resolve_worker_id(),
                lease_id=self._resolve_lease_id(),
                run_id=run_id,
                namespace=namespace,
            )
        except Exception as exc:
            raise RuntimeError(f"释放跨机归属键失败: run_id={run_id}") from exc

    def _uses_gateway_ownership_rpc(self) -> bool:
        return getattr(self._transport, "mode", TransportMode.DIRECT) == TransportMode.GATEWAY

    async def _abort_for_ownership_failure(self, reason: str) -> None:
        self._polling = False
        runs = await self._state_manager.get_all()
        for info in runs:
            if info.state in (RunState.RUNNING, RunState.CANCELLING, RunState.PREPARING):
                await self.cancel(info.run_id, reason=reason)
        self._running = False

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
                raise

    async def _on_transport_lease_revoked(self, reason: str = "lease-revoked") -> None:
        """P1-GW-02 回调: transport 撤销 → 唤醒 renew + cancel_all 兜底。"""
        logger.warning("transport 报告 Lease 被撤销 (reason={}), 触发 engine cancel_all", reason)
        self.request_ownership_renew_now()
        try:
            cancelled = await self.cancel_all(reason=f"lease-revoked: {reason}")
            logger.warning("engine cancel_all 完成: {} run 被取消", cancelled)
        except Exception as exc:
            logger.exception("engine cancel_all 异常: {}", exc)

    def request_ownership_renew_now(self) -> None:
        """P1-GW-02: 请求立即 renew(loop 未启动时 no-op)。"""
        if self._ownership_renew_wakeup is not None:
            self._ownership_renew_wakeup.set()

    async def cancel_all(self, reason: str = "") -> int:
        """P1-GW-02: 取消所有 RUNNING/CANCELLING/PREPARING/QUEUED run,返回取消数。"""
        runs = await self._state_manager.get_all()
        cancellable = {RunState.RUNNING, RunState.CANCELLING, RunState.PREPARING, RunState.QUEUED}
        cancelled = 0
        for info in runs:
            if info.state not in cancellable:
                continue
            try:
                if await self.cancel(info.run_id, reason=reason or "engine.cancel_all"):
                    cancelled += 1
            except Exception as exc:
                logger.warning("cancel_all: cancel {} 失败: {}", info.run_id, exc)
        return cancelled

    async def _renew_active_run_ownership(self) -> None:
        runs = await self._state_manager.get_all()
        active_states = {RunState.RUNNING, RunState.CANCELLING, RunState.PREPARING}
        for info in runs:
            if info.state not in active_states:
                continue
            await self._renew_one_run_ownership(info.run_id)

    async def _renew_one_run_ownership(self, run_id: str) -> None:
        try:
            renewed = await self._renew_run_ownership(run_id)
        except Exception as exc:
            await self.cancel(run_id, reason="run ownership 续租失败")
            raise RuntimeError(f"ownership 续租失败: run_id={run_id}") from exc
        if not renewed:
            await self.cancel(run_id, reason="run ownership 已丢失")

    async def _renew_run_ownership(self, run_id: str) -> bool:
        ttl_ms = self._RUN_OWNERSHIP_TTL_SECONDS * MILLISECONDS_PER_SECOND
        if self._uses_gateway_ownership_rpc():
            return await self._transport.renew_run_ownership(run_id, ttl_ms)
        return await self._renew_direct_run_ownership(run_id, ttl_ms)

    async def _renew_direct_run_ownership(self, run_id: str, ttl_ms: int) -> bool:
        from antcode_core.application.services.workers.run_ownership_fence import (
            OwnershipOutcome,
            renew_run_ownership,
        )

        redis, namespace = self._direct_ownership_context()
        outcome = await renew_run_ownership(
            redis,
            worker_id=self._resolve_worker_id(),
            lease_id=self._resolve_lease_id(),
            run_id=run_id,
            ttl_ms=ttl_ms,
            namespace=namespace,
        )
        if outcome is OwnershipOutcome.LEASE_STALE:
            # 整机 lease 已换代：向上抛，让 renew 循环 abort 所有执行。
            raise RuntimeError(f"ownership renew 被拒: lease 已被新代际取代 run_id={run_id}")
        return outcome is OwnershipOutcome.ACQUIRED

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

        env_vars = getattr(task_msg, "environment", {}) or {}
        if isinstance(env_vars, dict) and "ANTCODE_RUNTIME_ENV" in env_vars:
            env_vars = dict(env_vars)
            env_vars.pop("ANTCODE_RUNTIME_ENV", None)

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

    # P1-26: 缩容 grace period(秒) —— worker 收到 drain 标记后, 最多等
    # 这么久让在途任务跑完; 超时才走 self.cancel(kill 子进程 + 上报
    # CANCELLED) 和 hard cancel。300s 大于常见短任务, 又不至于让配置更新
    # 长期挂起。运维要拉长可以基于 _policies.timeout.execution_timeout
    # 覆写这个常量。
    _SHRINK_DRAIN_GRACE_SECONDS = 300.0
    # hard cancel 前留给 self.cancel 走完 executor.cancel + 上报 CANCELLED
    # 链路的窗口。
    _SHRINK_CANCEL_TIMEOUT_SECONDS = 30.0

    async def _resize_workers(self, new_max: int) -> None:
        """动态调整并发 worker 数量

        P1-26: 缩容不再直接 task.cancel()。原实现在 worker 上有在途任务时
        cancel 会让 ProcessExecutor 的子进程孤儿化(asyncio 只 cancel 读
        pipe 的协程, 不 kill subprocess),后果:
        - 旧子进程继续跑 → 产生外部副作用(写库/发消息)但不再上报 / ACK
        - master 侧 dispatch_status 停在 DISPATCHED 不进终态
        - PEL reclaim 后新 worker 再次执行同 run_id → 双跑
        新实现走三段式优雅关闭: drain → 走 cancel 通道 → hard cancel 兜底。
        """
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
            await self._shrink_workers(-diff)

    async def _shrink_workers(self, drain_count: int) -> None:
        """P1-26: 三段式优雅缩容。

        阶段 1 (drain): 把要退的 worker task 塞进 _draining_worker_tasks,
                        worker 在下一轮起点自然退出;在途任务不受影响,
                        跑完 + 正常 ACK + 上报终态。
        阶段 2 (cancel):  超过 grace period 还没退, 找到该 worker 上仍在跑
                          的 run, 走 self.cancel(run_id) 触发 executor 的
                          _terminate_process(SIGTERM + grace + SIGKILL),
                          让 _execute_task 拿到 CANCELLED 结果, 走
                          _report_result 正常上报终态 + ACK PEL。
        阶段 3 (force):   还挂着就 task.cancel(), _worker_loop 的
                          CancelledError 分支兜底 kill + 上报。
        """
        if drain_count <= 0:
            return
        draining: list[asyncio.Task] = []
        for _ in range(drain_count):
            if not self._worker_tasks:
                break
            task = self._worker_tasks.pop()
            self._draining_worker_tasks.add(task)
            draining.append(task)
        if not draining:
            return
        logger.info(f"P1-26: 优雅缩容 {len(draining)} 个 worker (drain 阶段开始)")

        # 阶段 1: 等待 drain 自然退出
        _, pending = await asyncio.wait(draining, timeout=self._SHRINK_DRAIN_GRACE_SECONDS)
        if not pending:
            for task in draining:
                self._draining_worker_tasks.discard(task)
            logger.info("P1-26: 缩容完成(drain 阶段全部退出)")
            return

        logger.warning(
            f"P1-26: {len(pending)} 个 worker drain 超时, 进入 cancel 阶段 (grace={self._SHRINK_DRAIN_GRACE_SECONDS}s)"
        )
        # 阶段 2: 只取消归属于待退出 worker task 的 run，不能误伤保留
        # worker 上的在途任务。
        for task in pending:
            run_id = self._worker_run_ids.get(task)
            if not run_id:
                continue
            try:
                await self.cancel(run_id, reason="worker shrink")
            except Exception:
                logger.exception(f"P1-26: shrink cancel 失败: run_id={run_id}")

        _, still_pending = await asyncio.wait(pending, timeout=self._SHRINK_CANCEL_TIMEOUT_SECONDS)
        if still_pending:
            # 阶段 3: hard cancel 兜底。_worker_loop 的 CancelledError 分支
            # 会做 executor.cancel + _report_result_by_info(CANCELLED)。
            logger.warning(f"P1-26: {len(still_pending)} 个 worker cancel 超时, 强制 cancel")
            for task in still_pending:
                task.cancel()
            for task in still_pending:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        for task in draining:
            self._draining_worker_tasks.discard(task)
        logger.info("P1-26: 缩容完成")

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

        if not self._runtime_manager:
            raise RuntimeError("runtime_manager 或 runtime_spec 未配置")

        from antcode_worker.runtime.spec import LockSource, PythonSpec
        from antcode_worker.runtime.spec import RuntimeSpec as RuntimeSpecV2

        domain_spec = context.runtime_spec
        spec = RuntimeSpecV2()
        if domain_spec is not None:
            spec = RuntimeSpecV2(
                python_spec=PythonSpec(
                    version=domain_spec.python_version,
                    path=domain_spec.python_path,
                ),
                lock_source=LockSource(requirements=list(domain_spec.requirements)),
                constraints=list(domain_spec.constraints),
                extras=list(domain_spec.extras),
                env_vars=dict(domain_spec.env_vars),
            )
        return await self._runtime_manager.prepare(spec)

    def _build_fallback_plan(self, context: RunContext, payload: Any, runtime_handle: Any) -> Any:
        """无插件时的兜底执行计划"""
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
