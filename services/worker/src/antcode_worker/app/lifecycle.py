"""
生命周期管理

负责 Worker 的启动和关闭流程。

Requirements: 2.5
"""

import asyncio
import time
from collections.abc import Callable
from typing import Any

from loguru import logger

from antcode_worker.adaptive_limits import current_memory_budget
from antcode_worker.app.shutdown import shutdown_components
from antcode_worker.container_scratch import validate_container_scratch_fits_budget
from antcode_worker.transport.base import WorkerState

MILLISECONDS_PER_SECOND = 1000


class Lifecycle:
    """
    生命周期管理器

    管理 Worker 组件的启动和关闭顺序。

    Requirements: 2.5
    """

    def __init__(self):
        self._startup_hooks: list[Callable] = []
        self._shutdown_hooks: list[Callable] = []
        self._running = False
        self._shutdown_event: asyncio.Event | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    def on_startup(self, hook: Callable) -> None:
        """注册启动钩子"""
        self._startup_hooks.append(hook)

    def on_shutdown(self, hook: Callable) -> None:
        """注册关闭钩子（后注册先执行）"""
        self._shutdown_hooks.insert(0, hook)

    async def startup(self, container: Any) -> None:
        """按依赖顺序启动 Worker；任一阶段失败均回滚已启动组件。"""
        logger.info("开始启动 Worker...")
        self._shutdown_event = asyncio.Event()
        self._validate_startup_dependencies(container)

        try:
            await self._check_optional_runtime()
            self._bind_transport_state(container)
            await self._start_base_components(container)
            await self._start_lease_heartbeat(container)
            await self._start_component(container.engine, "引擎")
            self._mark_ready(container)
            await self._run_startup_hooks()
            self._running = True
            logger.info("Worker 启动完成")
        except Exception as e:
            logger.error(f"启动失败: {e}")
            await self._rollback_startup(container)
            raise

    def _validate_startup_dependencies(self, container: Any) -> None:
        if getattr(container, "transport", None) is None:
            raise RuntimeError("Worker 缺少 transport")
        if getattr(container, "heartbeat_reporter", None) is None:
            raise RuntimeError("Worker 缺少 heartbeat reporter")
        # 容器自己的 /tmp 等内存盘可能是内核按**宿主内存的一半**建的（compose 的 tmpfs
        # 声明漏了 size=）。Worker 改不了自己容器的挂载，所以只能在接活之前拒绝——放行
        # 等于带着一个与容器额度无关的内存盘跑，正是 container_scratch 要消灭的静默失败。
        validate_container_scratch_fits_budget(current_memory_budget())

    async def _check_optional_runtime(self) -> None:
        """mise 是可选能力；检测失败只禁用多语言任务。"""
        try:
            from antcode_worker.runtime.mise_bootstrap import ensure_mise

            await ensure_mise()
        except Exception as exc:
            logger.error("mise 启动检测异常（不阻断 Worker 启动，多语言任务将不可用）: {}", exc)

    async def _start_base_components(self, container: Any) -> None:
        await self._start_transport(container.transport)
        await self._start_component(container.runtime_manager, "运行时管理器")
        await self._start_component(container.executor, "执行器")
        await self._start_observability(container)

    async def _start_transport(self, transport: Any) -> None:
        if not await transport.start():
            raise RuntimeError("传输层启动失败")
        logger.info("传输层已启动")

    async def _start_component(self, component: Any, label: str) -> None:
        if component is None:
            return
        await component.start()
        logger.info(f"{label}已启动")

    async def _start_observability(self, container: Any) -> None:
        server = container.observability_server
        if server is None:
            return
        host = getattr(container.config, "host", "0.0.0.0")
        port = getattr(container.config, "port", 8001)
        await server.start(host=host, port=port)

    async def _start_lease_heartbeat(self, container: Any) -> None:
        lease_interval = await self._initial_lease_renew(container)
        configured_interval = getattr(container.config, "heartbeat_interval", 30)
        interval = min(configured_interval, lease_interval)
        # 续期循环终止 = 租约不再续，必须让整个 Worker 停机；否则本进程会带着
        # 已失效的租约继续执行 run，而 master 已把同一个 run 补派给别人。
        container.heartbeat_reporter.set_fatal_error_handler(container.engine.record_fatal_error)
        await container.heartbeat_reporter.start(interval=interval)
        logger.info("心跳上报已启动")

    def _mark_ready(self, container: Any) -> None:
        if container.observability_server is None:
            return
        container.observability_server.set_ready(container.transport.is_connected)

    async def _run_startup_hooks(self) -> None:
        for hook in self._startup_hooks:
            result = hook()
            if asyncio.iscoroutine(result):
                await result

    async def _rollback_startup(self, container: Any) -> None:
        self._running = True
        await self.shutdown(container, grace_period=0)

    # X3: 初始 lease 获取的重试参数。kill -9 后 supervisor 在旧 lease TTL
    # （LeasePolicy.ttl_ms，默认 30s）内重启 Worker 时，lease grant Lua 会
    # 因"未过期的旧 self lease"返回 conflict —— 这不是永久故障，等旧 lease
    # 自然过期即可自愈。因此启动侧在 TTL + 余量的有界窗口内退避重试，
    # 避免 Worker 陷入 crash-loop 被有界重启策略打死。
    INITIAL_LEASE_RETRY_INTERVAL_SECONDS = 2.0
    INITIAL_LEASE_WAIT_MARGIN_SECONDS = 10.0
    DEFAULT_LEASE_TTL_SECONDS = 30.0

    async def _initial_lease_renew(self, container: Any) -> int:
        """Worker 启动时主动跑一次 ``transport.lease_renew("")``。

        - Direct 模式直接写 ``LeaseStore``，Gateway 模式调用 Lease RPC。
        - Gateway 模式跑 ``ControlService.Lease`` RPC，让 Master 立刻在
          ``lease:active`` 看到 Worker。
        - X3: 初始租约失败（conflict / revoked / RPC 失败等）不再一击致命：
          在旧 lease TTL + 余量的窗口内退避重试，等旧 lease 过期后自动恢复；
          窗口耗尽仍失败才拒绝启动，保证启动总时长有界。没有租约的 Worker
          无法通过结果 fencing，继续启动只会制造永远停在 running 的任务。
        """
        transport = container.transport
        if transport is None or not hasattr(transport, "lease_renew"):
            raise RuntimeError("Worker transport 不支持 lease_renew")

        deadline = time.monotonic() + self._initial_lease_wait_window_seconds(container)
        while True:
            try:
                return await self._attempt_initial_lease(transport)
            except Exception as exc:
                if time.monotonic() + self.INITIAL_LEASE_RETRY_INTERVAL_SECONDS > deadline:
                    raise RuntimeError(f"Worker 初始 lease 获取失败（重试窗口耗尽）: {exc}") from exc
                logger.warning("初始 lease 获取失败，等待旧 lease 过期后重试: {}", exc)
                await asyncio.sleep(self.INITIAL_LEASE_RETRY_INTERVAL_SECONDS)

    def _initial_lease_wait_window_seconds(self, container: Any) -> float:
        """初始 lease 重试窗口 = 旧 lease TTL + 余量。

        TTL 优先从 Direct transport 的 ``LeaseStore.policy.ttl_ms`` 读；
        拿不到（如 Gateway 模式，TTL 在 Master 侧）就用默认常量兜底。
        """
        ttl_seconds = self.DEFAULT_LEASE_TTL_SECONDS
        policy = getattr(
            getattr(getattr(container, "transport", None), "_lease_store", None),
            "policy",
            None,
        )
        try:
            ttl_ms = int(getattr(policy, "ttl_ms", 0) or 0)
        except (TypeError, ValueError):
            ttl_ms = 0
        if ttl_ms > 0:
            ttl_seconds = ttl_ms / MILLISECONDS_PER_SECOND
        return ttl_seconds + self.INITIAL_LEASE_WAIT_MARGIN_SECONDS

    async def _attempt_initial_lease(self, transport: Any) -> int:
        """单次初始 lease 获取；任一校验不通过即 raise，由上层决定是否重试。"""
        try:
            lease_id, expires_at_ms, renew_after_ms, revoked = await transport.lease_renew(
                current_lease_id="",
                metrics=None,
            )
        except Exception as exc:
            # 必须带上原因：重试日志只打这条消息的文本，吞掉 cause 就等于让
            # 崩溃重启的容器说不出自己为什么起不来（如控制面回的"契约版本过旧，请升级"）。
            raise RuntimeError(f"Worker 初始 lease_renew 失败: {exc}") from exc

        if revoked:
            raise RuntimeError("Worker 初始 lease 已被撤销")
        if not lease_id:
            raise RuntimeError("Worker 初始 lease_renew 未返回 lease_id")
        if expires_at_ms <= 0:
            raise RuntimeError("Worker 初始 lease 缺少有效过期时间")
        # P2 §4.3: 不能拿本机 time.time() 与 Redis 绝对过期时间比较——
        # 时钟偏移会把有效 lease 误判过期（或反之）。Direct 模式用
        # LeaseStore.is_current（Redis PTTL 权威时钟）复核；Gateway 模式
        # lease 由 Master 按 Redis TIME 授予，服务端已保证新签 lease 有效。
        await self._require_lease_current_authoritative(transport, lease_id)
        if renew_after_ms < MILLISECONDS_PER_SECOND:
            raise RuntimeError("Worker lease renew_after_ms 必须至少为 1000")

        logger.info(
            "初始 lease 已获取: expires_at_ms={} renew_after_ms={}",
            expires_at_ms,
            renew_after_ms,
        )
        return renew_after_ms // MILLISECONDS_PER_SECOND

    @staticmethod
    async def _require_lease_current_authoritative(transport: Any, lease_id: str) -> None:
        from antcode_core.application.services.lease_service import LeaseStore

        store = getattr(transport, "_lease_store", None)
        if not isinstance(store, LeaseStore):
            # Gateway 模式：lease 由服务端（Master 走 Redis TIME）授予，
            # 本地没有权威 LeaseStore，可信任新签结果。
            return
        worker_id = getattr(transport, "_worker_id", "") or ""
        if not await store.is_current(worker_id, lease_id):
            raise RuntimeError("Worker 初始 lease 在权威时钟(Redis PTTL)下无效")

    def _bind_transport_state(self, container: Any) -> None:
        """绑定传输层状态变更回调"""
        if not container or not container.transport:
            return

        async def _on_state_change(old_state: WorkerState, new_state: WorkerState) -> None:
            if container.observability_server:
                container.observability_server.set_ready(new_state == WorkerState.ONLINE)

            if new_state == WorkerState.ONLINE:
                logger.info("传输层已恢复在线")
            elif old_state == WorkerState.ONLINE and new_state != WorkerState.ONLINE:
                logger.warning("传输层离线")

        container.transport.on_state_change(_on_state_change)

    async def shutdown(self, container: Any, grace_period: float = 30.0) -> None:
        """按启动逆序停止组件并发送关闭信号。"""
        if not self._running:
            return
        logger.info(f"开始关闭 Worker (grace_period={grace_period}s)...")
        self._running = False

        try:
            await shutdown_components(container, grace_period, self._shutdown_hooks)
            logger.info("Worker 已关闭")
        finally:
            if self._shutdown_event:
                self._shutdown_event.set()

    async def wait_for_shutdown(self) -> None:
        """等待关闭信号"""
        if self._shutdown_event:
            await self._shutdown_event.wait()

    def trigger_shutdown(self) -> None:
        """触发关闭"""
        if self._shutdown_event:
            self._shutdown_event.set()
