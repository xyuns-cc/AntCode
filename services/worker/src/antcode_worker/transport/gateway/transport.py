"""Gateway transport over typed ControlService and DataService gRPC streams.

The internal ``TransportBase`` contract stays transport-independent; this module
owns lease fencing, subscriptions, acknowledgements, status, logs, and artifacts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from antcode_contracts.transcode import encode_capabilities
from antcode_core.common.log_limits import (
    DEFAULT_LOG_MAX_BATCH_BYTES,
    DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES,
    LogBatchLimits,
    positive_env_int,
)
from antcode_core.observability.tracing import (
    extract_traceparent,
    inject_trace,
)
from loguru import logger

from antcode_worker.transport.base import (
    ControlMessage,
    HeartbeatMessage,
    LogMessage,
    ServerConfig,
    TaskMessage,
    TaskResult,
    TransportBase,
    TransportMode,
    WorkerState,
)
from antcode_worker.transport.gateway.heartbeat_metrics import (
    build_metrics_proto,
    heartbeat_metrics_dict,
)
from antcode_worker.transport.gateway.runtime_control_clock import GatewayRuntimeControlClock
from antcode_worker.transport.gateway.status_error_policy import (
    GatewayStatusErrorPolicy,
    is_lease_fence_error,
    is_permanent_control_result_error,
)
from antcode_worker.transport.gateway.subscriptions import (
    CONTROL_SUBSCRIPTION,
    SUBSCRIPTION_NAMES,
    TASK_SUBSCRIPTION,
    SubscriptionHooks,
    SubscriptionRetryConfig,
    SubscriptionRunner,
)
from antcode_worker.transport.lease_cadence import MIN_LEASE_RENEW_AFTER_MS, MIN_LEASE_TTL_MS, read_lease_fields

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from grpc import aio as grpc_aio

    from antcode_worker.transport.gateway.auth import GatewayAuthenticator
    from antcode_worker.transport.gateway.reconnect import ReconnectManager


@dataclass(frozen=True)
class _GatewayConnection:
    channel: Any
    control_stub: Any
    data_stub: Any
    artifact_stub: Any
    lease_id: str


class _LeaseRevokedError(RuntimeError):
    """The current Worker process may never acquire another lease generation."""


# P2-#36: 用一个独立 sentinel 对象代替每秒 wait_for(timeout=1.0) 轮询。
# ``stop()`` 显式把 sentinel 放进 outbox，``_gen()`` 一识别就立刻 return,
# 客户端流随之关闭。和 ``None`` 区分开以免和合法消息混淆。
@dataclass
class GatewayConfig:
    """Gateway 传输层配置"""

    # 连接配置
    gateway_host: str = "localhost"
    gateway_port: int = 50051

    # TLS 配置
    use_tls: bool = False
    ca_cert_path: str | None = None
    client_cert_path: str | None = None
    client_key_path: str | None = None
    server_name_override: str | None = None

    # gRPC 配置
    max_send_message_length: int = 50 * 1024 * 1024  # 50MB
    max_receive_message_length: int = 50 * 1024 * 1024  # 50MB
    log_max_batch_bytes: int = field(
        default_factory=lambda: positive_env_int("GATEWAY_LOG_MAX_BATCH_BYTES", DEFAULT_LOG_MAX_BATCH_BYTES)
    )
    log_max_entry_content_bytes: int = field(
        default_factory=lambda: positive_env_int(
            "GATEWAY_LOG_MAX_ENTRY_CONTENT_BYTES",
            DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES,
        )
    )
    keepalive_time_ms: int = 30000
    keepalive_timeout_ms: int = 10000
    keepalive_permit_without_calls: bool = True

    # 超时配置
    connect_timeout: float = 10.0
    call_timeout: float = 30.0
    artifact_transfer_timeout: float = 300.0

    # 重连配置
    enable_reconnect: bool = True
    initial_backoff: float = 1.0
    max_backoff: float = 60.0
    backoff_multiplier: float = 2.0
    max_reconnect_attempts: int = 0  # 0 = 无限重试

    # 幂等性配置
    enable_receipt_idempotency: bool = True
    receipt_cache_ttl: float = 300.0  # 5 分钟

    # 认证配置
    auth_method: str = "api_key"  # api_key, mtls
    api_key: str | None = None
    worker_id: str | None = None
    task_payload_secret: str = ""

    # Streaming 配置
    task_prefetch: int = 1
    capabilities: list[str] = field(default_factory=list)

    # 队列上限
    task_queue_maxsize: int = 256
    status_queue_maxsize: int = 1024
    log_queue_maxsize: int = 4096
    control_queue_maxsize: int = 256

    # 额外选项
    extra_options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        LogBatchLimits(
            max_batch_bytes=self.log_max_batch_bytes,
            max_entry_content_bytes=self.log_max_entry_content_bytes,
        )


class GatewayTransport(GatewayStatusErrorPolicy, TransportBase):
    """Gateway 传输层实现（P1b：ControlService + DataService 双 stub）"""

    def __init__(
        self,
        gateway_config: GatewayConfig | None = None,
        config: ServerConfig | None = None,
    ):
        super().__init__(config)
        self._gateway_config = gateway_config or GatewayConfig()
        self._lease_capabilities: dict[str, str] = {}

        # gRPC 组件
        self._channel: grpc_aio.Channel | None = None
        self._control_stub: Any = None
        self._data_stub: Any = None
        self._artifact_stub: Any = None
        self._connect_lock = asyncio.Lock()

        # 认证器
        self._authenticator: GatewayAuthenticator | None = None

        # 重连管理器
        self._reconnect_manager: ReconnectManager | None = None

        # 内部队列（streaming RPC 与 transport 接口之间的解耦）
        self._task_inbox: asyncio.Queue[Any] | None = None
        self._control_inbox: asyncio.Queue[Any] | None = None

        # 后台 streaming tasks
        self._task_subscriber: asyncio.Task | None = None
        self._control_subscriber: asyncio.Task | None = None
        self._subscription_health = dict.fromkeys(SUBSCRIPTION_NAMES, False)
        self._subscription_runner = SubscriptionRunner(
            SubscriptionRetryConfig(
                initial_backoff=self._gateway_config.initial_backoff,
                max_backoff=self._gateway_config.max_backoff,
                backoff_multiplier=self._gateway_config.backoff_multiplier,
            )
        )
        # 幂等性缓存
        self._receipt_cache: dict[str, tuple[float, Any]] = {}
        self._result_cache: dict[str, tuple[float, bool]] = {}

        # 连接状态
        self._connected = False
        self._last_heartbeat: datetime | None = None
        self._consecutive_failures = 0

        # 重连控制
        self._reconnecting = False
        self._auth_failure_count = 0
        self._max_auth_failures = 5
        self._stopping = False

        # Lease 状态（替代 SendHeartbeat）
        self._lease_id: str = ""
        self._lease_revoked = False
        self._lease_revocation_lock = asyncio.Lock()
        self._lease_revocation_started = False
        self._runtime_control_clock = GatewayRuntimeControlClock()

        # P1-GW-02: Lease 被撤销/换代时的对外回调 (由 engine 注册),让 engine
        # 立即 cancel_all + 唤醒 ownership renew, 而不是等下一个 600s 续租周期。
        self._lease_revoked_callback: Any | None = None

    @property
    def mode(self) -> TransportMode:
        return TransportMode.GATEWAY

    @property
    def gateway_config(self) -> GatewayConfig:
        return self._gateway_config

    @property
    def artifact_stub(self) -> Any:
        return self._artifact_stub

    def artifact_rpc_session(self) -> tuple[Any, str, str, tuple[tuple[str, str], ...]]:
        """Return one authenticated ArtifactService session snapshot."""
        return (
            self._require_artifact_stub(),
            self._gateway_config.worker_id or "",
            self._require_lease_id(),
            tuple(self._get_auth_metadata()),
        )

    @property
    def is_connected(self) -> bool:
        return self._connected and self._channel is not None and self._subscriptions_ready

    @property
    def _subscriptions_ready(self) -> bool:
        return all(self._subscription_health.values())

    # ==================== 生命周期 ====================

    async def start(self) -> bool:
        if self._running:
            return True
        if self._lease_revoked:
            logger.error("Gateway Worker 代际已被撤销，本进程拒绝重启")
            return False

        self._stopping = False
        try:
            await self._init_authenticator()
            await self._init_reconnect_manager()
            success = await self._connect()
            if not success:
                logger.error("Gateway 初始连接失败")
                await self._cleanup_failed_start()
                return False
            self._task_inbox = asyncio.Queue(maxsize=self._gateway_config.task_queue_maxsize)
            self._control_inbox = asyncio.Queue(maxsize=self._gateway_config.control_queue_maxsize)
            self._running = True
            if self._lease_id:
                self._start_subscriptions()
            await self._refresh_subscription_state()
            logger.info(
                f"Gateway 传输层已启动: {self._gateway_config.gateway_host}:{self._gateway_config.gateway_port}"
            )
            return True
        except Exception:
            logger.exception("Gateway 启动失败")
            await self._cleanup_failed_start()
            return False

    async def stop(self, grace_period: float = 5.0) -> None:
        self._stopping = True
        self._running = False
        await self._cancel_background_tasks()
        if self._reconnect_manager is not None:
            await self._reconnect_manager.stop()
        await self._disconnect()
        self._receipt_cache.clear()
        self._result_cache.clear()
        await self._set_state(WorkerState.OFFLINE)
        logger.info("Gateway 传输层已停止")

    async def _cleanup_failed_start(self) -> None:
        if self._reconnect_manager is not None:
            await self._reconnect_manager.stop()
        await self._disconnect()
        self._authenticator = None
        await self._set_state(WorkerState.OFFLINE)

    async def deregister(self, reason: str = "shutdown") -> None:
        """T7-B3b (P1-6): worker 优雅停机时调 gateway Deregister RPC。

        让 master 立即撤销 lease；不再等 TTL（30s）到期。RPC 失败仅告警不
        阻塞后续 stop 流程（即使 gateway 不可达 lease 也会自然过期）。

        P2-20: worker_id 只保存在 ``_gateway_config``（``ServerConfig`` 里
        没有该字段）。旧实现读 ``self._config.worker_id`` 会拿到 AttributeError
        或 None,导致 Deregister RPC 永远发不出去,worker 停机后仍要等 lease
        TTL(30s) 到期,期间 master 继续向已下线的 worker 派发任务。这里改成
        统一从 ``self._gateway_config.worker_id`` 读,与 ``lease_renew`` /
        ``ack_task`` / ``ack_control`` 等保持一致。
        """
        worker_id = self._gateway_config.worker_id or ""
        if not self._control_stub or not worker_id:
            return
        try:
            from antcode_contracts import control_pb2

            request = control_pb2.DeregisterRequest(
                worker_id=worker_id,
                reason=reason,
                lease_id=self._lease_id or "",
            )
            metadata = self._get_auth_metadata()
            # 加短超时，避免 gateway 假死时阻塞停机
            await asyncio.wait_for(
                self._control_stub.Deregister(request, metadata=metadata),
                timeout=3.0,
            )
            logger.info(f"worker Deregister 已发送 (reason={reason})")
        except Exception as exc:
            logger.warning(f"Deregister RPC 失败（不阻塞停机；lease 会自然过期）: {exc}")

    # ==================== 任务订阅 (StreamTasks) ====================

    async def _task_subscription_loop(self) -> None:
        """长连接订阅 ``DataService.StreamTasks``，把 TaskDispatch 投递到 inbox。"""
        from antcode_worker.transport.gateway.codecs import TaskDecoder

        await self._subscription_runner.run(
            TASK_SUBSCRIPTION,
            self._subscription_hooks(lambda on_open: self._consume_task_stream(TaskDecoder, on_open)),
        )

    async def _consume_task_stream(self, decoder: Any, on_open: Callable[[], Awaitable[None]]) -> bool:
        stub = self._data_stub
        if stub is None:
            raise RuntimeError("Gateway DataService stub 未就绪")
        return await self._consume_stream(
            lambda: stub.StreamTasks(
                self._build_subscribe_request(),
                metadata=self._get_auth_metadata(),
            ),
            lambda dispatch: self._deliver_task_dispatch(dispatch, decoder),
            on_open,
        )

    async def _deliver_task_dispatch(self, dispatch: Any, decoder: Any) -> None:
        try:
            task = decoder.decode(
                dispatch,
                worker_id=self._gateway_config.worker_id or "",
                worker_secret=self._gateway_config.task_payload_secret,
            )
            inbound_traceparent = extract_traceparent(dispatch)
            if inbound_traceparent:
                task.traceparent = inbound_traceparent  # type: ignore[attr-defined]
            if task.receipt:
                self._receipt_cache[task.receipt] = (
                    datetime.now().timestamp(),
                    task.task_id,
                )
            if self._task_inbox is not None:
                await self._task_inbox.put(task)
        except Exception as exc:
            logger.exception("StreamTasks 投递失败")
            receipt_value = getattr(dispatch, "receipt_id", "") or ""
            task_id_value = getattr(dispatch, "task_id", "") or ""
            receipt = receipt_value if isinstance(receipt_value, str) else ""
            task_id = task_id_value if isinstance(task_id_value, str) else ""
            if not receipt:
                raise
            self._receipt_cache[receipt] = (datetime.now().timestamp(), task_id)
            rejected = await self.ack_task(receipt, accepted=False, reason="invalid task dispatch")
            if not rejected:
                raise RuntimeError(f"坏任务投递拒绝失败: receipt={receipt}") from exc

    # ==================== 控制订阅 (WatchControl) ====================

    async def _control_subscription_loop(self) -> None:
        """长连接订阅 ``ControlService.WatchControl``，把 ControlEvent 投递到 inbox。"""
        from antcode_worker.transport.gateway.codecs import ControlDecoder

        await self._subscription_runner.run(
            CONTROL_SUBSCRIPTION,
            self._subscription_hooks(lambda on_open: self._consume_control_stream(ControlDecoder, on_open)),
        )

    async def _consume_control_stream(self, decoder: Any, on_open: Callable[[], Awaitable[None]]) -> bool:
        stub = self._control_stub
        if stub is None:
            raise RuntimeError("Gateway ControlService stub 未就绪")
        return await self._consume_stream(
            lambda: stub.WatchControl(
                self._build_watch_control_request(),
                metadata=self._get_auth_metadata(),
            ),
            lambda event: self._deliver_control_event(event, decoder),
            on_open,
        )

    async def _deliver_control_event(self, event: Any, decoder: Any) -> None:
        try:
            ctrl_msg = self._control_event_to_message(event, decoder)
            if ctrl_msg is None:
                await self._handle_non_engine_control(event)
                return
            if self._control_inbox is not None:
                await self._control_inbox.put(ctrl_msg)
        except Exception as exc:
            logger.exception("WatchControl 投递失败")
            if self._control_inbox is not None:
                await self._control_inbox.put(exc)

    # ==================== 订阅通用循环 ====================

    async def _consume_stream(
        self,
        open_stream: Callable[[], Any],
        deliver: Callable[[Any], Awaitable[None]],
        on_open: Callable[[], Awaitable[None]],
    ) -> bool:
        """打开一条 server-stream 并逐条投递，返回是否收到过消息。"""
        stream = open_stream()
        wait_for_connection = getattr(stream, "wait_for_connection", None)
        if wait_for_connection is not None:
            await wait_for_connection()
        await on_open()
        received = False
        async for item in stream:
            if not self._running:
                return received
            received = True
            await deliver(item)
        return received

    def _subscription_hooks(
        self,
        consume: Callable[[Callable[[], Awaitable[None]]], Awaitable[bool]],
    ) -> SubscriptionHooks:
        return SubscriptionHooks(
            is_running=lambda: self._running,
            consume=consume,
            set_health=self._set_subscription_health,
            revoke_lease=self._abort_lease_revocation,
        )

    async def _set_subscription_health(self, name: str, healthy: bool) -> None:
        if name not in self._subscription_health:
            raise ValueError(f"未知 Gateway 订阅: {name}")
        self._subscription_health[name] = healthy
        await self._refresh_subscription_state()

    async def _refresh_subscription_state(self) -> None:
        if not self._running:
            return
        state = WorkerState.ONLINE if self.is_connected else WorkerState.RECONNECTING
        await self._set_state(state)

    async def _handle_non_engine_control(self, event: Any) -> None:
        payload_kind = self._control_payload_kind(event)
        event_id = getattr(event, "event_id", "") or ""
        if not event_id:
            # 连 receipt 都没有的畸形事件才继续抛：没法 ACK，只能交给
            # 订阅循环记录异常。
            raise RuntimeError(f"ControlEvent 缺少 event_id，无法 ACK: kind={payload_kind}")
        if payload_kind == "ping":
            if not await self.ack_control(event_id):
                raise RuntimeError(f"ping ControlEvent ACK 失败: event_id={event_id}")
            return
        # W3: 未知 payload 直接 ack 掉，避免阻塞——版本偏斜时 Gateway 可能
        # 推送本 Worker 不认识的新事件类型；若在这里抛异常，事件永不 ACK，
        # Gateway 每次 WatchControl 重建都会重放 PEL，形成永久毒消息循环。
        logger.warning(f"未知 ControlEvent payload，直接 ACK 跳过: event_id={event_id} kind={payload_kind}")
        if not await self.ack_control(event_id):
            # ACK 失败不抛：事件留在 PEL，重投后会再次走到这里重试 ACK。
            logger.warning(f"未知 ControlEvent ACK 失败，等待 Gateway 重投后重试: event_id={event_id}")

    @staticmethod
    def _control_payload_kind(event: Any) -> str | None:
        try:
            return event.WhichOneof("payload")
        except Exception:
            return None

    def _control_event_to_message(self, event: Any, decoder: Any) -> ControlMessage | None:
        """``control_pb2.ControlEvent`` → 内部 ``ControlMessage``"""
        event_id = getattr(event, "event_id", "") or ""
        payload_kind = self._control_payload_kind(event)

        if payload_kind == "task_cancel":
            data = decoder.decode_task_cancel(event.task_cancel)
            return ControlMessage(
                control_type="cancel",
                task_id=data.get("task_id", ""),
                run_id=data.get("run_id", ""),
                reason=data.get("reason", ""),
                receipt=event_id,
            )
        if payload_kind == "config_update":
            cfg = decoder.decode_config_update(event.config_update)
            return ControlMessage(
                control_type="config_update",
                payload=cfg,
                receipt=event_id,
            )
        if payload_kind == "runtime_control":
            return self._runtime_control_clock.to_message(event, decoder)
        if payload_kind == "ping":
            # ping 不投递给 engine；订阅循环统一负责 ACK。
            return None
        return None

    async def authoritative_now_ms(self) -> int:
        """使用控制事件携带的 Redis TIME 校准 Gateway Worker 时钟。"""
        return self._runtime_control_clock.now_ms()

    # ==================== TransportBase 实现：任务面 ====================

    async def poll_task(self, timeout: float = 5.0) -> TaskMessage | None:
        """从内部 task inbox 出队（由 ``_task_subscription_loop`` 投递）。"""
        if not self._running or self._task_inbox is None:
            return None
        try:
            task = await asyncio.wait_for(self._task_inbox.get(), timeout=timeout)
            if isinstance(task, Exception):
                raise task
            return task
        except TimeoutError:
            return None
        except Exception:
            logger.exception("poll_task 出队失败")
            raise

    async def ack_task(self, task_id: str, accepted: bool, reason: str = "") -> bool:
        """``DataService.AckTask``：unary 应答任务接受/拒绝。

        ``task_id`` 参数在 TransportBase 语义里其实是 ``receipt_id``
        （由 ``poll_task`` 返回的 ``TaskMessage.receipt`` 携带），
        Proto 上 ``receipt_id`` 与 ``task_id`` 是分开的两个字段。
        """
        if not self._data_stub or not self._running:
            return False

        receipt_id = task_id  # 兼容 TransportBase 调用方语义
        cache_key = f"ack:{receipt_id}"
        if self._gateway_config.enable_receipt_idempotency:
            cached = self._get_cached_result(cache_key)
            if cached is not None:
                logger.debug(f"使用缓存的 ACK 结果: {receipt_id}")
                return cached

        try:
            from antcode_contracts import data_pb2

            actual_task_id = self._get_receipt_task_id(receipt_id)
            request = data_pb2.AckTaskRequest(
                worker_id=self._gateway_config.worker_id or "",
                receipt_id=receipt_id,
                task_id=actual_task_id or "",
                accepted=accepted,
                reason=reason or "",
                # P1-GW-01: 结算携带当前代际 lease；旧代际进程的 ACK/requeue
                # 会被 Gateway FAILED_PRECONDITION 拒绝。
                lease_id=self._lease_id or "",
            )
            response = await asyncio.wait_for(
                self._data_stub.AckTask(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )
            success = bool(response.success)
            # 复审 D2: 只缓存成功结果。缓存 False 会把服务端瞬时失败
            # （如 Redis 抖动）固化 300s，封死结算重试通道。
            if success and self._gateway_config.enable_receipt_idempotency:
                self._cache_result(cache_key, success)
            if success:
                self._receipt_cache.pop(receipt_id, None)
            self._consecutive_failures = 0
            return success
        except Exception as e:
            self._consecutive_failures += 1
            logger.exception("确认任务失败")
            await self._handle_connection_error(e)
            return False

    async def defer_task(self, receipt: str, reason: str = "") -> bool:
        """Gateway 已把任务放入 PEL；本地丢弃缓存即可等待服务端重投。"""
        if "|" not in receipt:
            return False
        self._receipt_cache.pop(receipt, None)
        logger.info(f"任务保留在 Gateway PEL 等待重投: receipt={receipt} reason={reason}")
        return True

    async def requeue_task(self, receipt: str, reason: str = "") -> bool:
        """重新入队 = ack(accepted=False)，与 Direct 模式语义一致。"""
        return await self.ack_task(receipt, accepted=False, reason=reason)

    async def report_result(self, result: TaskResult) -> bool:
        """报告任务结果：走 ``DataService.StreamStatus`` 单条 client-stream，
        阻塞等真实 ``StatusAck``。

        P1-25 修复:
        --------
        旧实现走的是 outbox → ``_status_push_loop`` → 长连接 ``StreamStatus``
        的路径。虽然 B4 用 ``sent_event`` 等 generator ``yield`` 出去，但
        gRPC ``yield msg`` 完成只代表消息 flush 到本地 HTTP/2 send buffer,
        **服务端是否落 Redis 未确认** —— server 侧 handler 是 client-stream,
        只有整条流关闭时才返回 ``StatusAck``, 中间任何一条持久化失败会
        ``abort(UNAVAILABLE)``, 但那时 ``sent_event`` 已经被 set 了,
        engine 认为上报成功 → XACK 源消息 → 结果丢失。

        修复策略: 每次上报开一条 **单元素 client-stream**, 只 yield 这一条
        ``TaskStatus``, 然后关闭发送端等 server 回 ``StatusAck``:
          * ``ack.received >= 1`` → server 已经把这条 TaskStatus 落到
            ``task:result`` Redis stream (见 gateway data_service.StreamStatus 实现)
            → 返回 True, engine 可以安全 XACK。
          * 任何异常 / ``received == 0`` → 返回 False, engine 保留 PEL,
            下一轮 reclaim 重试。
        代价: 每个结果上报多一次 stream 建立/关闭的 RTT; 结果上报是低频
        (每个任务一次), 换来的精确 ack 语义值得。日志侧继续走 outbox
        长连接, 不受影响。
        """
        if not self._running:
            logger.warning(f"上报结果失败: 传输未运行 task_id={result.task_id}")
            return False
        if not self._data_stub:
            logger.warning(f"上报结果失败: data_stub 未就绪 task_id={result.task_id}")
            return False

        # 幂等性：相同 run + 相同状态 已经上报过就跳过。
        #
        # 契约修复: key 必须含 run_id 和 status，不能只用 task_id ——
        # engine._report_running_start 会先用同一个 task_id 上报一次
        # status="running"（P1-17 心跳），若 key 只含 task_id，这次 running
        # 上报会把 ``result:{task_id}`` 缓存为 True（TTL 300s），任务在
        # 5 分钟内跑完时最终终态上报直接命中缓存被跳过 —— engine 拿到
        # True 后 XACK + 清状态，master 永远收不到终态，结果凭空丢失。
        # 含 run_id 同时避免同 task 5 分钟内重跑（不同 run_id）被误吞。
        cache_key = f"result:{result.run_id}:{result.task_id}:{result.status}"
        if self._gateway_config.enable_receipt_idempotency:
            cached = self._get_cached_result(cache_key)
            if cached is not None:
                logger.debug(f"使用缓存的结果上报: {result.task_id}")
                return cached

        try:
            from antcode_worker.transport.gateway.codecs import TaskStatusEncoder

            msg = TaskStatusEncoder.encode(result, self._gateway_config.worker_id or "")
            # P5.4: 透传当前 trace。``engine._worker_loop`` 在任务起点
            # set_current_trace,出站时 inject_trace 把 trace 写到
            # TaskStatus.trace,Master ResultLoop 解码后可以把状态变更
            # 关联到同一个分布式 trace。
            inject_trace(msg)

            async def _one_shot():
                # 单条 client-stream: yield 一次即关闭发送端, 触发 server 回
                # StatusAck。gRPC AsyncIO 语义: async generator 正常 return
                # 表示 half-close (client done sending), server 处理完剩余
                # 请求后返回 unary response。
                yield msg

            try:
                ack = await asyncio.wait_for(
                    self._data_stub.StreamStatus(
                        _one_shot(),
                        metadata=self._get_auth_metadata(),
                    ),
                    timeout=self._gateway_config.call_timeout,
                )
            except TimeoutError:
                logger.warning(
                    f"report_result StreamStatus 超时"
                    f"({self._gateway_config.call_timeout}s), "
                    f"引擎将保留 PEL 等 reclaim: task_id={result.task_id}"
                )
                self._consecutive_failures += 1
                return False
            except Exception as exc:
                return await self._handle_status_report_error(result, exc)

            received = int(getattr(ack, "received", 0) or 0)
            if received < 1:
                # server 返回了但没确认落库(极少见, 可能是 abort 早于第一次
                # handle), 视为失败让 engine 重试。
                logger.warning(
                    f"report_result StatusAck.received={received}, "
                    f"服务端未确认持久化, 视为失败: task_id={result.task_id}"
                )
                return False

            self._consecutive_failures = 0
            if self._gateway_config.enable_receipt_idempotency:
                self._cache_result(cache_key, True)
            return True
        except Exception:
            logger.exception("上报任务状态失败")
            return False

    async def report_spider_data(
        self,
        run_id: str,
        items: list[dict[str, Any]],
    ) -> bool:
        if not items:
            return True
        batch = self._build_spider_item_batch(run_id, items)
        return await self._send_spider_batch(batch, len(items))

    async def update_spider_meta(
        self,
        run_id: str,
        meta: dict[str, Any],
    ) -> bool:
        from antcode_contracts import data_pb2

        update = self._build_spider_meta_update(meta, data_pb2)
        batch = data_pb2.SpiderDataBatch(
            worker_id=self._gateway_config.worker_id or "",
            run_id=run_id,
            project_id=str(meta.get("project_id", "")),
            lease_id=self._lease_id,
            meta=update,
        )
        return await self._send_spider_batch(batch, 0)

    @staticmethod
    def _build_spider_meta_update(meta: dict[str, Any], data_pb2: Any) -> Any:
        fields: dict[str, Any] = {
            "status": str(meta.get("status", "")),
            "last_item_at": str(meta.get("last_item_at", "")),
            "spider_name": str(meta.get("spider_name", "")),
            "started_at": str(meta.get("started_at", "")),
            "finished_at": str(meta.get("finished_at", "")),
            "config": str(meta.get("config", "")),
            "errors": str(meta.get("errors", "")),
        }
        for name in ("items_count", "pages_count", "errors_count"):
            value = meta.get(name)
            if value is not None and value != "":
                fields[name] = int(value)
        duration_ms = meta.get("duration_ms")
        if duration_ms is not None and duration_ms != "":
            fields["duration_ms"] = float(duration_ms)
        return data_pb2.SpiderMetaUpdate(**fields)

    def _build_spider_item_batch(
        self,
        run_id: str,
        items: list[dict[str, Any]],
    ) -> Any:
        from antcode_contracts import data_pb2

        encoded = [self._encode_spider_item(item, data_pb2) for item in items]
        return data_pb2.SpiderDataBatch(
            worker_id=self._gateway_config.worker_id or "",
            run_id=run_id,
            project_id=str(items[0].get("project_id", "")),
            lease_id=self._lease_id,
            items=encoded,
        )

    @staticmethod
    def _encode_spider_item(item: dict[str, Any], data_pb2: Any) -> Any:
        data = item.get("data", "")
        encoded_data = data if isinstance(data, bytes) else str(data).encode()
        return data_pb2.SpiderDataItem(
            item_id=str(item.get("item_id", "")),
            spider_name=str(item.get("spider_name", "")),
            item_type=str(item.get("item_type", "default")),
            data=encoded_data,
            url=str(item.get("url", "")),
            timestamp=str(item.get("timestamp", "")),
            sequence=int(item.get("sequence", 0) or 0),
        )

    async def _send_spider_batch(self, batch: Any, expected_items: int) -> bool:
        if not self._running or not self._data_stub:
            return False
        from antcode_core.spider_ingest import SpiderIngestLimits

        limits = SpiderIngestLimits.from_env()
        if len(batch.items) > limits.max_batch_items:
            raise ValueError(f"SpiderData batch items 超限: {len(batch.items)}")
        if batch.ByteSize() > limits.max_batch_bytes:
            raise ValueError(f"SpiderData batch bytes 超限: {batch.ByteSize()}")
        if any(item.ByteSize() > limits.max_item_bytes for item in batch.items):
            raise ValueError("SpiderData item 编码大小超限")
        inject_trace(batch)
        try:
            ack = await asyncio.wait_for(
                self._data_stub.StreamSpiderData(
                    self._one_spider_batch(batch),
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )
        except Exception as exc:
            logger.warning(f"StreamSpiderData 上报失败: run_id={batch.run_id} exc={exc}")
            await self._handle_connection_error(exc)
            return False
        return int(ack.accepted) == expected_items and int(ack.failed) == 0

    @staticmethod
    async def _one_spider_batch(batch: Any):
        yield batch

    # ==================== TransportBase 实现：日志面 ====================

    async def send_log(self, log: LogMessage) -> bool:
        return await self.send_log_batch([log])

    async def send_log_batch(self, logs: list[LogMessage]) -> bool:
        if not self._running or not self._data_stub:
            return False
        if not logs:
            return True

        from antcode_worker.transport.log_batches import (
            build_log_batches,
            is_invalid_argument_error,
            iter_log_batches,
        )

        limits = LogBatchLimits(
            max_batch_bytes=self._gateway_config.log_max_batch_bytes,
            max_entry_content_bytes=self._gateway_config.log_max_entry_content_bytes,
        )
        batches = build_log_batches(
            logs,
            worker_id=self._gateway_config.worker_id or "",
            lease_id=self._require_lease_id(),
            limits=limits,
        )
        try:
            ack = await asyncio.wait_for(
                self._data_stub.StreamLogs(
                    iter_log_batches(batches),
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )
            received = int(getattr(ack, "received", 0) or 0)
            if received != len(logs):
                raise RuntimeError(f"日志 ACK 数量不匹配: expected={len(logs)} received={received}")
            return True
        except ValueError:
            raise
        except Exception as exc:
            if is_invalid_argument_error(exc):
                raise ValueError(f"Gateway 拒绝日志批次: {exc}") from exc
            logger.exception("日志批次持久化失败")
            return False

    # ==================== TransportBase 实现：心跳/Lease ====================

    async def send_heartbeat(self, heartbeat: HeartbeatMessage) -> bool:
        """心跳 — P3 桥接到 ``lease_renew``（触发 ``ControlService.Lease``
        RPC 并缓存返回的 lease 元数据）；``revoked=True`` 视为心跳失败。"""
        if not self._running:
            return False

        metrics_dict = self._heartbeat_to_metrics_dict(heartbeat)
        capabilities = encode_capabilities(getattr(heartbeat, "capabilities", None))
        self._lease_capabilities = capabilities
        try:
            new_lease_id, _exp, _renew, revoked = await self.lease_renew(
                current_lease_id=self._lease_id,
                metrics=metrics_dict,
                capabilities=capabilities,
            )
            if revoked:
                return False
            if new_lease_id:
                self._last_heartbeat = datetime.now()
            return bool(new_lease_id)
        except Exception as exc:
            self._consecutive_failures += 1
            logger.exception("Lease/心跳失败")
            await self._handle_connection_error(exc)
            return False

    def _heartbeat_to_metrics_dict(self, heartbeat: HeartbeatMessage) -> dict:
        """``HeartbeatMessage`` → ``LeaseRequest.metrics`` 等价 dict。"""
        return heartbeat_metrics_dict(heartbeat)

    async def lease_renew(
        self,
        current_lease_id: str,
        metrics: dict | None = None,
        capabilities: dict[str, str] | None = None,
    ) -> tuple[str, int, int, bool]:
        """Gateway 模式 lease 续租：调用 ``ControlService.Lease``。

        Returns:
            ``(new_lease_id, expires_at_ms, renew_after_ms, revoked)``。
            RPC 失败或 worker_id 未配置时返回 ``("", 0, 0, False)``，
            ``revoked=True`` 由服务端透出（``LeaseResponse.revoked``）。
        """
        if not self._control_stub or not self._running:
            return ("", 0, 0, False)
        worker_id = self._gateway_config.worker_id or ""
        if not worker_id:
            return ("", 0, 0, False)

        try:
            from antcode_contracts import control_pb2

            metrics_msg = build_metrics_proto(metrics or {})
            request = control_pb2.LeaseRequest(
                worker_id=worker_id,
                current_lease_id=current_lease_id or "",
                metrics=metrics_msg,
                capabilities=capabilities if capabilities is not None else self._lease_capabilities,
            )
            response = await asyncio.wait_for(
                self._control_stub.Lease(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )

            lease = read_lease_fields(response)
            # 权威 TTL 与节拍成对缓存；校验与显式失败在 HeartbeatReporter 侧，
            # 因为本方法的宽 except 会把这里抛出的错吞成一次"续期失败"。
            self.adopt_server_lease_window(lease.ttl_ms, lease.renew_after_ms)

            # P1-GW-03: 换代=旧代际已死；按撤销永久停机 fail-closed。
            if lease.lease_id and self._lease_id and lease.lease_id != self._lease_id:
                logger.error(f"Lease 代际切换被拒绝: old={self._lease_id} new={lease.lease_id}，本进程按撤销停机")
                await self._abort_lease_revocation()
                return ("", 0, 0, True)
            if lease.revoked:
                logger.warning(f"Lease 被服务端撤销: reason={getattr(response, 'revoke_reason', '')}")
                if self._lease_id:
                    await self._abort_lease_revocation()
                return ("", lease.expires_at_ms, lease.renew_after_ms, True)
            if lease.lease_id:
                self._lease_id = lease.lease_id
                self._start_subscriptions()
            self._consecutive_failures = 0
            return (lease.lease_id, lease.expires_at_ms, lease.renew_after_ms, lease.revoked)
        except Exception as exc:
            self._consecutive_failures += 1
            logger.exception("lease_renew RPC 失败")
            await self._handle_connection_error(exc)
            return ("", 0, 0, False)

    def _start_subscriptions(self) -> None:
        if self._task_subscriber is None or self._task_subscriber.done():
            self._task_subscriber = asyncio.create_task(self._task_subscription_loop())
        if self._control_subscriber is None or self._control_subscriber.done():
            self._control_subscriber = asyncio.create_task(self._control_subscription_loop())

    # ==================== TransportBase 实现：控制面 ====================

    async def poll_control(self, timeout: float = 5.0) -> ControlMessage | None:
        if not self._running or self._control_inbox is None:
            return None
        try:
            msg = await asyncio.wait_for(self._control_inbox.get(), timeout=timeout)
            if isinstance(msg, Exception):
                raise msg
            return msg
        except TimeoutError:
            return None
        except Exception:
            logger.exception("poll_control 出队失败")
            raise

    async def claim_run_ownership(self, run_id: str, ttl_ms: int) -> bool:
        return await self._ownership_call(
            run_id,
            rpc_name="ClaimRunOwnership",
            request_cls="RunOwnershipClaimRequest",
            response_field="acquired",
            ttl_ms=ttl_ms,
        )

    async def renew_run_ownership(self, run_id: str, ttl_ms: int) -> bool:
        return await self._ownership_call(
            run_id,
            rpc_name="RenewRunOwnership",
            request_cls="RunOwnershipRenewRequest",
            response_field="renewed",
            ttl_ms=ttl_ms,
        )

    async def release_run_ownership(self, run_id: str) -> bool:
        return await self._ownership_call(
            run_id,
            rpc_name="ReleaseRunOwnership",
            request_cls="RunOwnershipReleaseRequest",
            response_field="released",
        )

    async def _ownership_call(
        self,
        run_id: str,
        *,
        rpc_name: str,
        request_cls: str,
        response_field: str,
        ttl_ms: int | None = None,
    ) -> bool:
        """Run ownership 三个 RPC 的公共实现：构建请求 + 调用 + 取响应布尔位。"""
        from antcode_contracts import artifact_pb2

        stub = self._require_artifact_stub()
        fields: dict[str, Any] = {
            "worker_id": self._gateway_config.worker_id or "",
            "lease_id": self._require_lease_id(),
            "run_id": run_id,
        }
        if ttl_ms is not None:
            fields["ttl_ms"] = ttl_ms
        request = getattr(artifact_pb2, request_cls)(**fields)
        response = await asyncio.wait_for(
            getattr(stub, rpc_name)(request, metadata=self._get_auth_metadata()),
            timeout=self._gateway_config.call_timeout,
        )
        return bool(getattr(response, response_field))

    def _require_artifact_stub(self) -> Any:
        if not self._running or self._artifact_stub is None:
            raise RuntimeError("Gateway ArtifactService stub 未初始化")
        return self._artifact_stub

    def _require_lease_id(self) -> str:
        if not self._lease_id:
            raise RuntimeError("Gateway run ownership 缺少当前 lease_id")
        return self._lease_id

    async def _ack_control_rpc(
        self,
        receipt: str,
        *,
        success: bool = True,
        error: str = "",
        request_id: str | None = None,
        data_json: str | None = None,
    ) -> bool:
        """构建并发送 ``AckControlRequest``，返回 ``response.received``。

        只封装公共的请求构建 + RPC 调用；异常处理策略由两个调用方自行
        决定（见 ``ack_control`` / ``send_control_result`` 内注释）。
        """
        from antcode_contracts import control_pb2
        from antcode_core.common.error_messages import normalize_persisted_error_message

        request = control_pb2.AckControlRequest(
            worker_id=self._gateway_config.worker_id or "",
            event_id=receipt,
            success=bool(success),
            error=normalize_persisted_error_message(error, max_bytes=16 * 1024) or "",
            request_id=request_id or "",
            data_json=data_json or "",
            lease_id=self._lease_id,
        )
        response = await asyncio.wait_for(
            self._control_stub.AckControl(
                request,
                metadata=self._get_auth_metadata(),
            ),
            timeout=self._gateway_config.call_timeout,
        )
        return bool(getattr(response, "received", False))

    async def ack_control(self, receipt: str) -> bool:
        """``ControlService.AckControl(event_id=receipt, success=True)``。"""
        if not self._control_stub or not self._running:
            return False

        try:
            return await self._ack_control_rpc(receipt)
        except Exception as e:
            # 有意保留的行为差异：普通 ACK 吞掉错误只返回 False，不计
            # _consecutive_failures，也不甄别永久错误——事件留在 PEL 由
            # Gateway 重投即可；带结果结算的 send_control_result 才需要
            # 失败计数 + 永久错误上抛。
            logger.exception("确认控制消息失败")
            await self._handle_connection_error(e)
            return False

    async def send_control_result(
        self,
        request_id: str,
        reply_stream: str,  # noqa: ARG002 - Gateway 从原始事件安全派生 reply stream
        success: bool,
        *,
        receipt: str = "",
        data: Any = None,
        error: str = "",
    ) -> bool:
        """通过 AckControl 原子回传运行时结果并确认原控制事件。"""
        if not self._control_stub or not self._running:
            return False
        if not receipt:
            raise ValueError("Gateway 运行时控制结果缺少原控制事件 receipt")

        # W2: 序列化必须放在网络 try 之外。data 含 datetime/bytes/NaN 时
        # json.dumps 抛 TypeError/ValueError，这是本地永久性错误——若混进
        # 下面的网络异常分支会被计入 _consecutive_failures（3 次即拆掉健康
        # 连接触发重连），且 engine 拿同一个不可序列化 payload 无限重试。
        # 降级为失败结果照常结算原控制事件，让事件得到 ACK。
        try:
            data_json = json.dumps(data, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            logger.warning(f"控制结果序列化失败，降级为失败结算: request_id={request_id} exc={exc}")
            note = f"结果序列化失败: {exc}"
            error = f"{error}; {note}" if error else note
            success = False
            data_json = "null"

        try:
            return await self._ack_control_rpc(
                receipt,
                success=bool(success),
                error=error,
                request_id=request_id,
                data_json=data_json,
            )
        except Exception as e:
            # 有意保留的行为差异（相对 ack_control）：结果结算失败要计
            # _consecutive_failures 并甄别永久错误——永久拒绝直接上抛，
            # 让 engine 停止重试同一结算。
            if is_permanent_control_result_error(e):
                raise RuntimeError(f"Gateway 永久拒绝运行时控制结果: {e}") from e
            self._consecutive_failures += 1
            logger.exception("回传控制结果失败")
            await self._handle_connection_error(e)
            return False

    # ==================== 凭证/状态 ====================

    def set_credentials(self, worker_id: str, api_key: str | None = None) -> None:
        self._gateway_config.worker_id = worker_id
        if api_key:
            self._gateway_config.api_key = api_key
        if self._authenticator is not None:
            self._authenticator = self._build_authenticator()

    def set_capabilities(self, capabilities: object) -> None:
        self._lease_capabilities = encode_capabilities(capabilities)

    def get_status(self) -> dict[str, Any]:
        reconnect_stats = None
        if self._reconnect_manager:
            reconnect_stats = self._reconnect_manager.get_stats().to_dict()

        return {
            "mode": self.mode.value,
            "state": self._state.value,
            "running": self._running,
            "connected": self._connected,
            "subscription_health": dict(self._subscription_health),
            "gateway_host": self._gateway_config.gateway_host,
            "gateway_port": self._gateway_config.gateway_port,
            "use_tls": self._gateway_config.use_tls,
            "auth_method": self._gateway_config.auth_method,
            "worker_id": self._gateway_config.worker_id,
            "lease_id": self._lease_id,
            "lease_revoked": self._lease_revoked,
            "last_heartbeat": (self._last_heartbeat.isoformat() if self._last_heartbeat else None),
            "consecutive_failures": self._consecutive_failures,
            "reconnect_stats": reconnect_stats,
        }

    async def reconnect(self) -> bool:
        return await self._connect()

    # ==================== 私有：初始化 ====================

    async def _init_authenticator(self) -> None:
        self._authenticator = self._build_authenticator()

    def _build_authenticator(self) -> GatewayAuthenticator:
        from antcode_worker.transport.gateway.auth import (
            AuthConfig,
            AuthMethod,
            GatewayAuthenticator,
        )

        auth_method = AuthMethod(self._gateway_config.auth_method)
        auth_config = AuthConfig(
            method=auth_method,
            api_key=self._gateway_config.api_key,
            worker_id=self._gateway_config.worker_id,
            client_cert_path=self._gateway_config.client_cert_path,
            client_key_path=self._gateway_config.client_key_path,
        )
        return GatewayAuthenticator(auth_config)

    async def _init_reconnect_manager(self) -> None:
        if not self._gateway_config.enable_reconnect:
            return

        from antcode_worker.transport.gateway.reconnect import (
            ReconnectConfig,
            ReconnectManager,
        )

        reconnect_config = ReconnectConfig(
            initial_backoff=self._gateway_config.initial_backoff,
            max_backoff=self._gateway_config.max_backoff,
            backoff_multiplier=self._gateway_config.backoff_multiplier,
            max_attempts=self._gateway_config.max_reconnect_attempts,
        )
        self._reconnect_manager = ReconnectManager(
            reconnect_config,
            connect_func=self._connect,
        )
        self._reconnect_manager.on_reconnect_success(self._restore_online_after_reconnect)
        await self._reconnect_manager.start()

    async def _restore_online_after_reconnect(self) -> None:
        if not self._running or self._lease_revoked:
            return
        self._consecutive_failures = 0
        await self._refresh_subscription_state()

    async def _connect(self) -> bool:
        target = self._gateway_target()
        error: Exception | None = None
        old_channel: Any = None
        async with self._connect_lock:
            if self._stopping:
                return False
            try:
                connection = await self._build_candidate_connection(target)
                if self._stopping:
                    await self._close_channel(connection.channel)
                    return False
                old_channel = self._install_connection(connection)
            except Exception as exc:
                error = exc
        if error is not None:
            await self._handle_connect_failure(target, error)
            return False
        await self._close_channel(old_channel)
        logger.info(f"Gateway 连接成功: {target}")
        return True

    def _gateway_target(self) -> str:
        from antcode_worker.gateway_endpoint import parse_gateway_address

        return parse_gateway_address(
            endpoint="",
            host=self._gateway_config.gateway_host,
            port=self._gateway_config.gateway_port,
        ).target

    async def _build_candidate_connection(self, target: str) -> _GatewayConnection:
        channel = self._create_channel(target)
        try:
            await asyncio.wait_for(channel.channel_ready(), timeout=self._gateway_config.connect_timeout)
            control_stub, data_stub, artifact_stub = self._create_stubs(channel)
            await self._check_protocol_capabilities(control_stub)
            lease_id = await self._validate_reconnect_lease(control_stub)
            return _GatewayConnection(channel, control_stub, data_stub, artifact_stub, lease_id)
        except BaseException:
            await self._close_channel(channel)
            raise

    def _create_channel(self, target: str) -> Any:
        from grpc import aio as grpc_aio

        options = self._channel_options()
        if not self._gateway_config.use_tls:
            return grpc_aio.insecure_channel(target, options=options)
        return grpc_aio.secure_channel(target, self._create_tls_credentials(), options=options)

    def _channel_options(self) -> list[tuple[str, Any]]:
        config = self._gateway_config
        options = [
            ("grpc.max_send_message_length", config.max_send_message_length),
            ("grpc.max_receive_message_length", config.max_receive_message_length),
            ("grpc.keepalive_time_ms", config.keepalive_time_ms),
            ("grpc.keepalive_timeout_ms", config.keepalive_timeout_ms),
            ("grpc.keepalive_permit_without_calls", config.keepalive_permit_without_calls),
        ]
        options.extend(config.extra_options.items())
        return options

    async def _check_protocol_capabilities(self, control_stub: Any = None) -> None:
        """Fail closed when Gateway cannot persist runtime-control results."""
        from antcode_contracts import control_pb2

        stub = control_stub or self._control_stub
        if stub is None:
            raise RuntimeError("Gateway ControlService stub 未初始化")
        request = control_pb2.CapabilitiesRequest(
            worker_id=self._gateway_config.worker_id or "",
        )
        response = await asyncio.wait_for(
            stub.GetCapabilities(request, metadata=self._get_auth_metadata()),
            timeout=self._gateway_config.call_timeout,
        )
        if not response.runtime_control_results_v1:
            raise RuntimeError("Gateway 不支持 runtime_control_results_v1，拒绝启动")
        if not response.runtime_control_lease_fencing_v1:
            raise RuntimeError("Gateway 不支持 runtime_control_lease_fencing_v1，拒绝启动")
        if not response.runtime_control_deadline_v1:
            raise RuntimeError("Gateway 不支持 runtime_control_deadline_v1，拒绝启动")
        if not response.artifact_transfer_v1:
            raise RuntimeError("Gateway 不支持 artifact_transfer_v1，拒绝启动")
        if not response.runtime_control_authoritative_clock_v1:
            raise RuntimeError("Gateway 不支持 runtime_control_authoritative_clock_v1，拒绝启动")
        if not response.worker_capabilities_v1:
            raise RuntimeError("Gateway 不支持 worker_capabilities_v1，拒绝启动")

    async def _validate_reconnect_lease(self, control_stub: Any) -> str:
        if not self._running:
            return self._lease_id
        if self._lease_revoked:
            raise _LeaseRevokedError("Gateway Worker 代际已被撤销")
        if not self._lease_id:
            raise RuntimeError("Gateway 重连时缺少当前 lease_id")
        from antcode_contracts import control_pb2

        request = control_pb2.LeaseRequest(
            worker_id=self._gateway_config.worker_id or "",
            current_lease_id=self._lease_id,
            capabilities=self._lease_capabilities,
        )
        response = await asyncio.wait_for(
            control_stub.Lease(request, metadata=self._get_auth_metadata()),
            timeout=self._gateway_config.call_timeout,
        )
        if bool(getattr(response, "revoked", False)):
            self._lease_revoked = True
        # P1-GW-01: 重连时 Gateway 派新 Lease 视为旧代际被剥夺，触发 self-fence 而非无缝覆盖，避免旧内存状态与新 L 争抢 ownership/PEL 双执行。
        new_lease_id = getattr(response, "lease_id", "") or ""
        if self._lease_id and new_lease_id and new_lease_id != self._lease_id:
            logger.warning(
                "Gateway 重连返回新 Lease {} != 本地 {},视为旧代际被剥夺,触发 self-fence",
                new_lease_id,
                self._lease_id,
            )
            self._lease_revoked = True
            raise _LeaseRevokedError(f"Gateway 派新 Lease {new_lease_id},本地 {self._lease_id} 已被剥夺")
        return self._require_valid_lease_response(response)

    @staticmethod
    def _require_valid_lease_response(response: Any) -> str:
        lease = read_lease_fields(response)
        if lease.revoked:
            raise _LeaseRevokedError("Gateway 重连 lease 已被撤销")
        # Lease RPC 已在 Gateway 端用 Redis TIME 完成存活性判定。Worker 与
        # Gateway 可能存在时钟偏移，不能再用本机 wall clock 否定服务端刚刚
        # 确认并续租成功的代际；这里只校验响应合同是否完整。
        if not lease.lease_id or lease.expires_at_ms < 1:
            raise RuntimeError("Gateway 重连未取得有效 lease")
        if lease.renew_after_ms < MIN_LEASE_RENEW_AFTER_MS:
            raise RuntimeError("Gateway 重连 lease 续租周期无效")
        # 权威 TTL 必须随 lease 下发：缺了它 Worker 只能按本地默认值校验
        # renew*2 < ttl，服务端收紧 TTL 时守卫会静默通过（双执行）。
        if lease.ttl_ms < MIN_LEASE_TTL_MS:
            raise RuntimeError("Gateway 重连 lease 缺少权威 ttl_ms")
        return lease.lease_id

    def _install_connection(self, connection: _GatewayConnection) -> Any:
        old_channel = self._channel
        self._channel = connection.channel
        self._control_stub = connection.control_stub
        self._data_stub = connection.data_stub
        self._artifact_stub = connection.artifact_stub
        self._lease_id = connection.lease_id
        self._connected = True
        self._subscription_health = dict.fromkeys(SUBSCRIPTION_NAMES, False)
        self._auth_failure_count = 0
        return old_channel

    async def _disconnect(self) -> None:
        async with self._connect_lock:
            channel = self._channel
            self._connected = False
            self._subscription_health = dict.fromkeys(SUBSCRIPTION_NAMES, False)
            self._channel = None
            self._control_stub = None
            self._data_stub = None
            self._artifact_stub = None
        await self._close_channel(channel)

    @staticmethod
    async def _close_channel(channel: Any) -> None:
        if channel is None:
            return
        try:
            await channel.close()
        except Exception as exc:
            logger.warning(f"关闭 channel 时出错: {exc}")

    def _create_tls_credentials(self) -> Any:
        import grpc

        root_certs = None
        private_key = None
        certificate_chain = None

        if self._gateway_config.ca_cert_path:
            root_certs = Path(self._gateway_config.ca_cert_path).read_bytes()

        if self._gateway_config.client_cert_path and self._gateway_config.client_key_path:
            certificate_chain = Path(self._gateway_config.client_cert_path).read_bytes()
            private_key = Path(self._gateway_config.client_key_path).read_bytes()

        return grpc.ssl_channel_credentials(
            root_certificates=root_certs,
            private_key=private_key,
            certificate_chain=certificate_chain,
        )

    def _create_stubs(self, channel: Any = None) -> tuple[Any, Any, Any]:
        from antcode_contracts import artifact_pb2_grpc, control_pb2_grpc, data_pb2_grpc

        active_channel = channel or self._channel
        if active_channel is None:
            raise RuntimeError("Gateway channel 未初始化")
        control_stub = control_pb2_grpc.ControlServiceStub(active_channel)
        data_stub = data_pb2_grpc.DataServiceStub(active_channel)
        artifact_stub = artifact_pb2_grpc.ArtifactServiceStub(active_channel)
        return control_stub, data_stub, artifact_stub

    def _get_auth_metadata(self) -> list[tuple[str, str]]:
        if self._authenticator:
            return self._authenticator.get_metadata()
        return []

    # ==================== 私有：请求构建 ====================

    def _build_subscribe_request(self) -> Any:
        from antcode_contracts import data_pb2

        return data_pb2.SubscribeRequest(
            worker_id=self._gateway_config.worker_id or "",
            capabilities=list(self._gateway_config.capabilities),
            prefetch=int(self._gateway_config.task_prefetch or 1),
            lease_id=self._require_lease_id(),
        )

    def _build_watch_control_request(self) -> Any:
        from antcode_contracts import control_pb2

        return control_pb2.WatchControlRequest(
            worker_id=self._gateway_config.worker_id or "",
            lease_id=self._lease_id,
        )

    def _get_receipt_task_id(self, receipt_id: str) -> str:
        cached = self._receipt_cache.get(receipt_id)
        if not cached:
            return ""
        timestamp, task_id = cached
        now = datetime.now().timestamp()
        if now - timestamp > self._gateway_config.receipt_cache_ttl:
            self._receipt_cache.pop(receipt_id, None)
            return ""
        return str(task_id)

    # ==================== 私有：连接错误处理 ====================

    async def _handle_connection_error(self, error: Exception) -> None:
        """处理连接错误（保留原 P0 实现的认证退避 + 重连管理逻辑）。"""
        if is_lease_fence_error(error):
            logger.error("Gateway 拒绝已失效的 Worker Lease，本进程立即 self-fence")
            await self._abort_lease_revocation()
            return
        if self._is_auth_error(error):
            self._auth_failure_count += 1
            if self._auth_failure_count >= self._max_auth_failures:
                logger.error(
                    f"认证连续失败 {self._auth_failure_count} 次，停止重连。请检查 WORKER_API_KEY 配置是否正确"
                )
                await self._abort_authentication()
                return
            backoff = min(30.0, 5.0 * self._auth_failure_count)
            logger.warning(
                f"认证失败 ({self._auth_failure_count}/{self._max_auth_failures})，"
                f"{backoff:.1f}秒后重试。请检查 WORKER_API_KEY 配置"
            )
            await asyncio.sleep(backoff)
            return

        self._auth_failure_count = 0

        if self._reconnecting:
            logger.debug("重连进行中，跳过")
            return

        if self._consecutive_failures >= 3:
            self._connected = False
            self._reconnecting = True
            try:
                logger.warning(f"连续失败 {self._consecutive_failures} 次，尝试重连")
                await self._set_state(WorkerState.RECONNECTING)

                if self._reconnect_manager:
                    self._reconnect_manager.notify_disconnected(str(error))
                    success = await self._reconnect_manager.wait_connected(timeout=120.0)
                    if success:
                        self._consecutive_failures = 0
                        await self._refresh_subscription_state()
                    else:
                        await self._set_state(WorkerState.OFFLINE)
                else:
                    backoff = min(60.0, 2.0 ** min(self._consecutive_failures, 6))
                    logger.info(f"等待 {backoff:.1f}秒后重连")
                    await asyncio.sleep(backoff)
                    success = await self._connect()
                    if success:
                        self._consecutive_failures = 0
                        await self._refresh_subscription_state()
                    else:
                        await self._set_state(WorkerState.OFFLINE)
            finally:
                self._reconnecting = False

    async def _handle_connect_failure(self, target: str, error: Exception) -> None:
        if isinstance(error, TimeoutError):
            logger.error(f"Gateway 连接超时: {target}")
        else:
            logger.error(f"Gateway 连接失败: {error}")
        if isinstance(error, _LeaseRevokedError):
            await self._abort_lease_revocation()
            return
        if not self._is_auth_error(error):
            return
        self._auth_failure_count += 1
        if self._auth_failure_count >= self._max_auth_failures:
            await self._abort_authentication()

    @staticmethod
    def _is_auth_error(error: Exception) -> bool:
        import grpc

        if not hasattr(error, "code") or not callable(error.code):
            return False
        with contextlib.suppress(Exception):
            return error.code() == grpc.StatusCode.UNAUTHENTICATED
        return False

    async def _abort_authentication(self) -> None:
        await self._halt_transport()
        self._authenticator = None

    def set_lease_revoked_callback(self, callback: Any) -> None:
        """P1-GW-02: engine 注册 Lease 撤销回调；撤销/换代时先调 callback (async) 让 engine cancel_all，再 halt transport。"""
        self._lease_revoked_callback = callback

    async def _abort_lease_revocation(self) -> None:
        async with self._lease_revocation_lock:
            if self._lease_revocation_started:
                return
            self._lease_revocation_started = True
            self._lease_revoked = True
            self._stopping = True
            self._running = False
            callback = self._lease_revoked_callback
            try:
                if callback is not None:
                    await callback("gateway-revoke")
            finally:
                await self._halt_transport()

    async def _halt_transport(self) -> None:
        self._stopping = True
        self._running = False
        await self._cancel_background_tasks()
        if self._reconnect_manager is not None:
            await self._reconnect_manager.stop()
        await self._disconnect()
        await self._set_state(WorkerState.OFFLINE)

    # ==================== 私有：幂等性缓存 ====================

    def _get_cached_result(self, cache_key: str) -> bool | None:
        if cache_key not in self._result_cache:
            return None
        timestamp, result = self._result_cache[cache_key]
        now = datetime.now().timestamp()
        if now - timestamp > self._gateway_config.receipt_cache_ttl:
            del self._result_cache[cache_key]
            return None
        return result

    def _cache_result(self, cache_key: str, result: bool) -> None:
        now = datetime.now().timestamp()
        self._result_cache[cache_key] = (now, result)
        self._cleanup_cache()

    def _cleanup_cache(self) -> None:
        now = datetime.now().timestamp()
        ttl = self._gateway_config.receipt_cache_ttl

        expired_keys = [key for key, (ts, _) in self._result_cache.items() if now - ts > ttl]
        for key in expired_keys:
            del self._result_cache[key]

        expired_keys = [key for key, (ts, _) in self._receipt_cache.items() if now - ts > ttl]
        for key in expired_keys:
            del self._receipt_cache[key]

    # ==================== 私有：后台任务取消 ====================

    async def _cancel_background_tasks(self) -> None:
        tasks = []
        current = asyncio.current_task()
        for task in (
            self._task_subscriber,
            self._control_subscriber,
        ):
            if task is not None and task is not current and not task.done():
                task.cancel()
                tasks.append(task)

        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        self._task_subscriber = None
        self._control_subscriber = None
