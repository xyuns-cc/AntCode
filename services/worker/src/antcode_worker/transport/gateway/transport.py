"""
Gateway 传输层实现（Gateway 模式，P1b 重写）

公网 Worker 通过 ``ControlService`` + ``DataService`` 两个 gRPC stub 接入。
所有数据面 RPC 现在都是 streaming：
- ``DataService.StreamTasks``：server-stream，Worker 订阅一次，Gateway 推
  ``TaskDispatch``。``poll_task`` 从内部队列出队。
- ``DataService.AckTask``：unary，每条任务回 ack。
- ``DataService.StreamStatus``：client-stream，Worker 推 ``TaskStatus``。
  ``report_result`` 把结果 put 到队列，后台任务真正发送。
- ``DataService.StreamLogs``：client-stream，Worker 推 ``LogBatch``。
  ``send_log`` / ``send_log_batch`` / ``send_log_chunk`` 全部归一到队列。
- ``ControlService.WatchControl``：server-stream，Gateway 推
  ``ControlEvent``；``poll_control`` 从内部队列出队。
- ``ControlService.AckControl``：unary，每条事件回 ack。
- ``ControlService.Lease``：unary，替代旧 SendHeartbeat（参数 metrics）。

注意：本文件**只动实现**，``TransportBase`` 接口签名不变（``send_heartbeat``
保留名字，内部走 Lease；``send_control_result`` 通过 AckControl 携带
``success`` / ``error`` 回报；``send_log_chunk`` 暂时归并入日志流，
chunked binary 后续 P3 走 source_bundle/pgartifact）。

Requirements: 5.5, 5.6, 5.7
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from grpc import aio as grpc_aio

    from antcode_worker.transport.gateway.auth import GatewayAuthenticator
    from antcode_worker.transport.gateway.reconnect import ReconnectManager


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
    keepalive_time_ms: int = 30000
    keepalive_timeout_ms: int = 10000
    keepalive_permit_without_calls: bool = True

    # 超时配置
    connect_timeout: float = 10.0
    call_timeout: float = 30.0

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


class GatewayTransport(TransportBase):
    """Gateway 传输层实现（P1b：ControlService + DataService 双 stub）"""

    def __init__(
        self,
        gateway_config: GatewayConfig | None = None,
        config: ServerConfig | None = None,
    ):
        super().__init__(config)
        self._gateway_config = gateway_config or GatewayConfig()

        # gRPC 组件
        self._channel: grpc_aio.Channel | None = None
        self._control_stub: Any = None
        self._data_stub: Any = None

        # 认证器
        self._authenticator: GatewayAuthenticator | None = None

        # 重连管理器
        self._reconnect_manager: ReconnectManager | None = None

        # 内部队列（streaming RPC 与 transport 接口之间的解耦）
        self._task_inbox: asyncio.Queue[Any] | None = None
        self._control_inbox: asyncio.Queue[Any] | None = None
        self._status_outbox: asyncio.Queue[Any] | None = None
        self._log_outbox: asyncio.Queue[Any] | None = None

        # 后台 streaming tasks
        self._task_subscriber: asyncio.Task | None = None
        self._control_subscriber: asyncio.Task | None = None
        self._status_pusher: asyncio.Task | None = None
        self._log_pusher: asyncio.Task | None = None

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

        # Lease 状态（替代 SendHeartbeat）
        self._lease_id: str = ""

    @property
    def mode(self) -> TransportMode:
        return TransportMode.GATEWAY

    @property
    def gateway_config(self) -> GatewayConfig:
        return self._gateway_config

    @property
    def is_connected(self) -> bool:
        return self._connected and self._channel is not None

    # ==================== 生命周期 ====================

    async def start(self) -> bool:
        if self._running:
            return True

        try:
            await self._init_authenticator()
            await self._init_reconnect_manager()

            success = await self._connect()
            if not success:
                logger.error("Gateway 初始连接失败")
                return False

            # 初始化队列
            self._task_inbox = asyncio.Queue(maxsize=self._gateway_config.task_queue_maxsize)
            self._control_inbox = asyncio.Queue(maxsize=self._gateway_config.control_queue_maxsize)
            self._status_outbox = asyncio.Queue(maxsize=self._gateway_config.status_queue_maxsize)
            self._log_outbox = asyncio.Queue(maxsize=self._gateway_config.log_queue_maxsize)

            self._running = True

            # 启动 streaming 后台任务（指数退避重连内置）
            self._task_subscriber = asyncio.create_task(self._task_subscription_loop())
            self._control_subscriber = asyncio.create_task(self._control_subscription_loop())
            self._status_pusher = asyncio.create_task(self._status_push_loop())
            self._log_pusher = asyncio.create_task(self._log_push_loop())

            await self._set_state(WorkerState.ONLINE)

            logger.info(
                f"Gateway 传输层已启动: "
                f"{self._gateway_config.gateway_host}:{self._gateway_config.gateway_port}"
            )
            return True

        except Exception as e:
            logger.error(f"Gateway 启动失败: {e}")
            return False

    async def stop(self, grace_period: float = 5.0) -> None:
        if not self._running:
            return

        self._running = False

        # 优雅 drain：等待 status / log 队列被推完（限定 grace_period 内）
        try:
            await asyncio.wait_for(
                self._drain_outbox_queues(),
                timeout=max(0.0, grace_period),
            )
        except TimeoutError:
            logger.warning("Gateway transport stop: outbox drain timeout，强制关闭")

        await self._cancel_background_tasks()
        await self._disconnect()

        self._receipt_cache.clear()
        self._result_cache.clear()

        await self._set_state(WorkerState.OFFLINE)
        logger.info("Gateway 传输层已停止")

    async def _drain_outbox_queues(self) -> None:
        """等待 status / log outbox 被推送完"""
        while True:
            if self._status_outbox is None and self._log_outbox is None:
                return
            status_empty = self._status_outbox is None or self._status_outbox.empty()
            log_empty = self._log_outbox is None or self._log_outbox.empty()
            if status_empty and log_empty:
                return
            await asyncio.sleep(0.05)

    # ==================== 任务订阅 (StreamTasks) ====================

    async def _task_subscription_loop(self) -> None:
        """长连接订阅 ``DataService.StreamTasks``，把 TaskDispatch 投递到 inbox。

        断开后按指数退避重连，重连失败不阻塞 transport（让 reconnect_manager
        / _handle_connection_error 接管）。
        """
        from antcode_worker.transport.gateway.codecs import TaskDecoder

        backoff = 1.0
        while self._running:
            try:
                if not self._data_stub:
                    await asyncio.sleep(backoff)
                    backoff = min(self._gateway_config.max_backoff, backoff * 2)
                    continue

                request = self._build_subscribe_request()
                stream = self._data_stub.StreamTasks(
                    request,
                    metadata=self._get_auth_metadata(),
                )
                # 重置 backoff（流建立成功）
                backoff = 1.0

                async for dispatch in stream:
                    if not self._running:
                        break
                    try:
                        task = TaskDecoder.decode(dispatch)
                        # P5.4: 从 ``TaskDispatch.trace`` 提取 traceparent
                        # 透传给 engine。TaskDecoder 当前不读 trace 字段
                        # (为保证 P3 codec 不动),这里 setattr 给 TaskMessage
                        # 挂一个动态属性,engine ``_worker_loop`` 会读取并
                        # set_current_trace,实现 Master → Worker 链路。
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
                        logger.error(f"StreamTasks 投递失败: {exc}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._running:
                    break
                logger.warning(f"StreamTasks 流断开，{backoff:.1f}s 后重连: {e}")
                await asyncio.sleep(backoff)
                backoff = min(self._gateway_config.max_backoff, backoff * 2)

    # ==================== 控制订阅 (WatchControl) ====================

    async def _control_subscription_loop(self) -> None:
        """长连接订阅 ``ControlService.WatchControl``，把 ControlEvent 投递到 inbox。"""
        from antcode_worker.transport.gateway.codecs import ControlDecoder

        backoff = 1.0
        while self._running:
            try:
                if not self._control_stub:
                    await asyncio.sleep(backoff)
                    backoff = min(self._gateway_config.max_backoff, backoff * 2)
                    continue

                request = self._build_watch_control_request()
                stream = self._control_stub.WatchControl(
                    request,
                    metadata=self._get_auth_metadata(),
                )
                backoff = 1.0

                async for event in stream:
                    if not self._running:
                        break
                    try:
                        ctrl_msg = self._control_event_to_message(event, ControlDecoder)
                        if ctrl_msg is None:
                            # 未知 payload 直接 ack 掉，避免阻塞
                            event_id = getattr(event, "event_id", "") or ""
                            if event_id:
                                with contextlib.suppress(Exception):
                                    await self.ack_control(event_id)
                            continue
                        if self._control_inbox is not None:
                            await self._control_inbox.put(ctrl_msg)
                    except Exception as exc:
                        logger.error(f"WatchControl 投递失败: {exc}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._running:
                    break
                logger.warning(f"WatchControl 流断开，{backoff:.1f}s 后重连: {e}")
                await asyncio.sleep(backoff)
                backoff = min(self._gateway_config.max_backoff, backoff * 2)

    def _control_event_to_message(
        self, event: Any, decoder: Any
    ) -> ControlMessage | None:
        """``control_pb2.ControlEvent`` → 内部 ``ControlMessage``"""
        event_id = getattr(event, "event_id", "") or ""
        try:
            payload_kind = event.WhichOneof("payload")
        except Exception:
            payload_kind = None

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
            rt = decoder.decode_runtime_control(event.runtime_control)
            # 兼容 engine：payload 顶层放 args，request_id/action 在外层
            return ControlMessage(
                control_type="runtime_manage",
                payload={
                    "request_id": rt.get("request_id", ""),
                    "action": rt.get("action", ""),
                    "params": rt.get("params", {}),
                    "args": rt.get("args", {}),
                    # 兼容 engine 旧路径继续读 ``payload.get("payload")``
                    "payload": rt.get("args", {}),
                },
                receipt=event_id,
            )
        if payload_kind == "ping":
            # ping 不投递给 engine，直接 ack
            with contextlib.suppress(Exception):
                if event_id:
                    asyncio.create_task(self.ack_control(event_id))
            return None
        return None

    # ==================== 状态/日志推送 (StreamStatus / StreamLogs) ====================

    async def _status_push_loop(self) -> None:
        """从 outbox 取 ``TaskStatus`` 推到 ``DataService.StreamStatus``。

        client-streaming RPC 在 worker 端是 ``call(generator)`` 的形式 —
        我们维护一个 async generator，从队列出队作为流元素。流断开时
        重新打开。
        """
        backoff = 1.0
        while self._running:
            try:
                if not self._data_stub or self._status_outbox is None:
                    await asyncio.sleep(backoff)
                    backoff = min(self._gateway_config.max_backoff, backoff * 2)
                    continue

                async def _gen():
                    while self._running:
                        try:
                            msg = await asyncio.wait_for(
                                self._status_outbox.get(),
                                timeout=1.0,
                            )
                        except TimeoutError:
                            continue
                        if msg is None:
                            return
                        yield msg

                ack = await self._data_stub.StreamStatus(
                    _gen(),
                    metadata=self._get_auth_metadata(),
                )
                logger.debug(f"StreamStatus 流结束: received={getattr(ack, 'received', 0)}")
                backoff = 1.0

            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._running:
                    break
                logger.warning(f"StreamStatus 流断开，{backoff:.1f}s 后重连: {e}")
                await asyncio.sleep(backoff)
                backoff = min(self._gateway_config.max_backoff, backoff * 2)

    async def _log_push_loop(self) -> None:
        """从 outbox 取 ``LogBatch`` 推到 ``DataService.StreamLogs``。"""
        backoff = 1.0
        while self._running:
            try:
                if not self._data_stub or self._log_outbox is None:
                    await asyncio.sleep(backoff)
                    backoff = min(self._gateway_config.max_backoff, backoff * 2)
                    continue

                async def _gen():
                    while self._running:
                        try:
                            msg = await asyncio.wait_for(
                                self._log_outbox.get(),
                                timeout=1.0,
                            )
                        except TimeoutError:
                            continue
                        if msg is None:
                            return
                        yield msg

                ack = await self._data_stub.StreamLogs(
                    _gen(),
                    metadata=self._get_auth_metadata(),
                )
                logger.debug(f"StreamLogs 流结束: received={getattr(ack, 'received', 0)}")
                backoff = 1.0

            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._running:
                    break
                logger.warning(f"StreamLogs 流断开，{backoff:.1f}s 后重连: {e}")
                await asyncio.sleep(backoff)
                backoff = min(self._gateway_config.max_backoff, backoff * 2)

    # ==================== TransportBase 实现：任务面 ====================

    async def poll_task(self, timeout: float = 5.0) -> TaskMessage | None:
        """从内部 task inbox 出队（由 ``_task_subscription_loop`` 投递）。"""
        if not self._running or self._task_inbox is None:
            return None
        try:
            task = await asyncio.wait_for(self._task_inbox.get(), timeout=timeout)
            return task
        except TimeoutError:
            return None
        except Exception as e:
            logger.error(f"poll_task 出队失败: {e}")
            return None

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
            )
            response = await asyncio.wait_for(
                self._data_stub.AckTask(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )
            success = bool(response.success)
            if self._gateway_config.enable_receipt_idempotency:
                self._cache_result(cache_key, success)
            if success:
                self._receipt_cache.pop(receipt_id, None)
            self._consecutive_failures = 0
            return success
        except Exception as e:
            self._consecutive_failures += 1
            logger.error(f"确认任务失败: {e}")
            await self._handle_connection_error(e)
            return False

    async def requeue_task(self, receipt: str, reason: str = "") -> bool:
        """重新入队 = ack(accepted=False)，与 Direct 模式语义一致。"""
        return await self.ack_task(receipt, accepted=False, reason=reason)

    async def report_result(self, result: TaskResult) -> bool:
        """报告任务结果：``TaskStatus`` 入 outbox，后台流推送。"""
        if not self._running:
            logger.warning(f"上报结果失败: 传输未运行 task_id={result.task_id}")
            return False
        if self._status_outbox is None:
            logger.warning(f"上报结果失败: status outbox 未就绪 task_id={result.task_id}")
            return False

        # 幂等性：相同 task_id 重复入队就跳过
        cache_key = f"result:{result.task_id}"
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
            await self._status_outbox.put(msg)
            if self._gateway_config.enable_receipt_idempotency:
                self._cache_result(cache_key, True)
            return True
        except Exception as e:
            logger.error(f"入队任务状态失败: {e}")
            return False

    # ==================== TransportBase 实现：日志面 ====================

    async def send_log(self, log: LogMessage) -> bool:
        return await self.send_log_batch([log])

    async def send_log_batch(self, logs: list[LogMessage]) -> bool:
        if not self._running or self._log_outbox is None:
            return False
        if not logs:
            return True

        try:
            from antcode_worker.transport.gateway.codecs import LogEncoder

            batch = LogEncoder.encode_batch(logs, self._gateway_config.worker_id or "")
            # P5.4: 透传当前 trace,Master 端 log ingester 解码后可以把
            # 这批日志按 trace_id 接到调用链上(全链路日志查询)。
            inject_trace(batch)
            await self._log_outbox.put(batch)
            return True
        except Exception as e:
            logger.error(f"入队日志批次失败: {e}")
            return False

    async def send_log_chunk(
        self,
        run_id: str,
        log_type: str,
        data: bytes,
        offset: int,
        is_final: bool = False,
    ) -> bool:
        """发送日志分片

        TODO(P3): 新协议下大块 binary 走 source_bundle/pgartifact，
        不再有专门的 chunk gRPC。短期内 P1b 把 chunk 包成 1-entry
        ``LogBatch``（content = base64(data)），实现层归一到日志流。
        """
        import base64

        encoded = base64.b64encode(data).decode("utf-8") if data else ""
        marker = "[FINAL]" if is_final else "[CHUNK]"
        log = LogMessage(
            run_id=run_id,
            log_type=log_type,
            content=f"{marker} offset={offset} {encoded}",
            timestamp=datetime.now(),
            sequence=int(offset),
        )
        return await self.send_log_batch([log])

    # ==================== TransportBase 实现：心跳/Lease ====================

    async def send_heartbeat(self, heartbeat: HeartbeatMessage) -> bool:
        """心跳 — P3 桥接到 ``lease_renew``，对外仍保留 send_heartbeat 名字。

        本方法把 ``HeartbeatMessage`` 摊平成 metrics dict，调用 ``lease_renew``
        实际触发 ``ControlService.Lease`` RPC，并把返回的 lease 元数据缓存在
        ``self._lease_id``。``revoked=True`` 视为心跳失败，调用方据此触发
        重新注册。
        """
        if not self._running:
            return False

        metrics_dict = self._heartbeat_to_metrics_dict(heartbeat)
        try:
            new_lease_id, _exp, _renew, revoked = await self.lease_renew(
                current_lease_id=self._lease_id,
                metrics=metrics_dict,
            )
            if revoked:
                return False
            if new_lease_id:
                self._last_heartbeat = datetime.now()
            return bool(new_lease_id)
        except Exception as exc:
            self._consecutive_failures += 1
            logger.error(f"Lease/心跳失败: {exc}")
            await self._handle_connection_error(exc)
            return False

    def _heartbeat_to_metrics_dict(self, heartbeat: HeartbeatMessage) -> dict:
        """``HeartbeatMessage`` → ``LeaseRequest.metrics`` 等价 dict。"""
        metrics = getattr(heartbeat, "metrics", None)
        if metrics is not None:
            return {
                "cpu": getattr(metrics, "cpu", 0.0),
                "memory": getattr(metrics, "memory", 0.0),
                "disk": getattr(metrics, "disk", 0.0),
                "running_tasks": getattr(metrics, "running_tasks", 0),
                "max_concurrent_tasks": getattr(metrics, "max_concurrent_tasks", 5),
            }
        return {
            "cpu": getattr(heartbeat, "cpu_percent", 0.0),
            "memory": getattr(heartbeat, "memory_percent", 0.0),
            "disk": getattr(heartbeat, "disk_percent", 0.0),
            "running_tasks": getattr(heartbeat, "running_tasks", 0),
            "max_concurrent_tasks": getattr(heartbeat, "max_concurrent_tasks", 5),
        }

    async def lease_renew(
        self,
        current_lease_id: str,
        metrics: dict | None = None,
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

            metrics_msg = self._build_metrics_proto(metrics or {})
            request = control_pb2.LeaseRequest(
                worker_id=worker_id,
                current_lease_id=current_lease_id or "",
                metrics=metrics_msg,
            )
            response = await asyncio.wait_for(
                self._control_stub.Lease(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )

            new_lease_id = getattr(response, "lease_id", "") or ""
            expires_at_ms = int(getattr(response, "expires_at_ms", 0) or 0)
            renew_after_ms = int(getattr(response, "renew_after_ms", 0) or 0)
            revoked = bool(getattr(response, "revoked", False))

            if new_lease_id:
                self._lease_id = new_lease_id
            if revoked:
                logger.warning(
                    f"Lease 被服务端撤销: reason={getattr(response, 'revoke_reason', '')}"
                )
                # 清空本地 lease，下一次会重新发租
                self._lease_id = ""
            else:
                self._consecutive_failures = 0
            return (new_lease_id, expires_at_ms, renew_after_ms, revoked)
        except Exception as exc:
            self._consecutive_failures += 1
            logger.error(f"lease_renew RPC 失败: {exc}")
            await self._handle_connection_error(exc)
            return ("", 0, 0, False)

    def _build_metrics_proto(self, metrics: dict):
        """``dict`` → ``common_pb2.Metrics``。缺失字段用 0 兜底。"""
        from antcode_contracts import common_pb2

        return common_pb2.Metrics(
            cpu=float(metrics.get("cpu", 0.0) or 0.0),
            memory=float(metrics.get("memory", 0.0) or 0.0),
            disk=float(metrics.get("disk", 0.0) or 0.0),
            running_tasks=int(metrics.get("running_tasks", 0) or 0),
            max_concurrent_tasks=int(metrics.get("max_concurrent_tasks", 5) or 5),
        )

    # ==================== TransportBase 实现：控制面 ====================

    async def poll_control(self, timeout: float = 5.0) -> ControlMessage | None:
        if not self._running or self._control_inbox is None:
            return None
        try:
            msg = await asyncio.wait_for(self._control_inbox.get(), timeout=timeout)
            return msg
        except TimeoutError:
            return None
        except Exception as e:
            logger.error(f"poll_control 出队失败: {e}")
            return None

    async def ack_control(self, receipt: str) -> bool:
        """``ControlService.AckControl(event_id=receipt, success=True)``。"""
        if not self._control_stub or not self._running:
            return False

        try:
            from antcode_contracts import control_pb2

            request = control_pb2.AckControlRequest(
                worker_id=self._gateway_config.worker_id or "",
                event_id=receipt,
                success=True,
                error="",
            )
            response = await asyncio.wait_for(
                self._control_stub.AckControl(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )
            return bool(getattr(response, "received", False))
        except Exception as e:
            logger.error(f"确认控制消息失败: {e}")
            await self._handle_connection_error(e)
            return False

    async def send_control_result(
        self,
        request_id: str,
        reply_stream: str,  # noqa: ARG002 - legacy 参数，新协议不需要
        success: bool,
        data: dict | None = None,  # noqa: ARG002 - typed map 暂未透传到 AckControl
        error: str = "",
    ) -> bool:
        """回传控制结果：新协议下走 ``AckControl(success, error)``。

        说明：
        - ``reply_stream`` 在新协议不再需要（双向流自带 correlation）。
        - 旧 ``data: dict`` 因为 ``AckControlResponse`` 没有结构化结果字段，
          这里只回传 ``success`` / ``error``，详细结果由 worker 自行落地。
        - request_id（即 ControlEvent.event_id）作为 ``event_id`` 回传。
        """
        if not self._control_stub or not self._running:
            return False

        try:
            from antcode_contracts import control_pb2

            request = control_pb2.AckControlRequest(
                worker_id=self._gateway_config.worker_id or "",
                event_id=request_id,
                success=bool(success),
                error=error or "",
            )
            response = await asyncio.wait_for(
                self._control_stub.AckControl(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )
            return bool(getattr(response, "received", False))
        except Exception as e:
            logger.error(f"回传控制结果失败: {e}")
            await self._handle_connection_error(e)
            return False

    # ==================== 凭证/状态 ====================

    def set_credentials(self, worker_id: str, api_key: str | None = None) -> None:
        self._gateway_config.worker_id = worker_id
        if api_key:
            self._gateway_config.api_key = api_key

    def get_status(self) -> dict[str, Any]:
        reconnect_stats = None
        if self._reconnect_manager:
            reconnect_stats = self._reconnect_manager.get_stats().to_dict()

        return {
            "mode": self.mode.value,
            "state": self._state.value,
            "running": self._running,
            "connected": self._connected,
            "gateway_host": self._gateway_config.gateway_host,
            "gateway_port": self._gateway_config.gateway_port,
            "use_tls": self._gateway_config.use_tls,
            "auth_method": self._gateway_config.auth_method,
            "worker_id": self._gateway_config.worker_id,
            "lease_id": self._lease_id,
            "last_heartbeat": (
                self._last_heartbeat.isoformat() if self._last_heartbeat else None
            ),
            "consecutive_failures": self._consecutive_failures,
            "reconnect_stats": reconnect_stats,
        }

    async def reconnect(self) -> bool:
        await self._disconnect()
        return await self._connect()

    # ==================== 私有：初始化 ====================

    async def _init_authenticator(self) -> None:
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
        self._authenticator = GatewayAuthenticator(auth_config)

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

    async def _connect(self) -> bool:
        target = f"{self._gateway_config.gateway_host}:{self._gateway_config.gateway_port}"

        try:
            from grpc import aio as grpc_aio

            options = [
                ("grpc.max_send_message_length", self._gateway_config.max_send_message_length),
                ("grpc.max_receive_message_length", self._gateway_config.max_receive_message_length),
                ("grpc.keepalive_time_ms", self._gateway_config.keepalive_time_ms),
                ("grpc.keepalive_timeout_ms", self._gateway_config.keepalive_timeout_ms),
                ("grpc.keepalive_permit_without_calls", self._gateway_config.keepalive_permit_without_calls),
            ]
            for key, value in self._gateway_config.extra_options.items():
                options.append((key, value))

            if self._gateway_config.use_tls:
                credentials = self._create_tls_credentials()
                self._channel = grpc_aio.secure_channel(
                    target, credentials, options=options,
                )
            else:
                self._channel = grpc_aio.insecure_channel(target, options=options)

            await asyncio.wait_for(
                self._channel.channel_ready(),
                timeout=self._gateway_config.connect_timeout,
            )

            self._control_stub, self._data_stub = self._create_stubs()

            self._connected = True
            logger.info(f"Gateway 连接成功: {target}")
            return True

        except TimeoutError:
            logger.error(f"Gateway 连接超时: {target}")
            return False
        except Exception as e:
            logger.error(f"Gateway 连接失败: {e}")
            return False

    async def _disconnect(self) -> None:
        self._connected = False

        if self._channel:
            try:
                await self._channel.close()
            except Exception as e:
                logger.warning(f"关闭 channel 时出错: {e}")
            finally:
                self._channel = None

        self._control_stub = None
        self._data_stub = None

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

    def _create_stubs(self) -> tuple[Any, Any]:
        from antcode_contracts import control_pb2_grpc, data_pb2_grpc

        control_stub = control_pb2_grpc.ControlServiceStub(self._channel)
        data_stub = data_pb2_grpc.DataServiceStub(self._channel)
        return control_stub, data_stub

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
        )

    def _build_watch_control_request(self) -> Any:
        from antcode_contracts import control_pb2

        return control_pb2.WatchControlRequest(
            worker_id=self._gateway_config.worker_id or "",
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
        import grpc

        is_auth_error = False
        if hasattr(error, "code") and callable(error.code):
            try:
                if error.code() == grpc.StatusCode.UNAUTHENTICATED:
                    is_auth_error = True
            except Exception:
                pass

        if is_auth_error:
            self._auth_failure_count += 1
            if self._auth_failure_count >= self._max_auth_failures:
                logger.error(
                    f"认证连续失败 {self._auth_failure_count} 次，停止重连。"
                    f"请检查 WORKER_API_KEY 配置是否正确"
                )
                await self._set_state(WorkerState.OFFLINE)
                self._running = False
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
            self._reconnecting = True
            try:
                logger.warning(f"连续失败 {self._consecutive_failures} 次，尝试重连")
                await self._set_state(WorkerState.RECONNECTING)

                if self._reconnect_manager:
                    self._reconnect_manager.notify_disconnected(str(error))
                    success = await self._reconnect_manager.wait_connected(timeout=120.0)
                    if success:
                        await self._set_state(WorkerState.ONLINE)
                        self._consecutive_failures = 0
                    else:
                        await self._set_state(WorkerState.OFFLINE)
                else:
                    backoff = min(60.0, 2.0 ** min(self._consecutive_failures, 6))
                    logger.info(f"等待 {backoff:.1f}秒后重连")
                    await asyncio.sleep(backoff)
                    success = await self._connect()
                    if success:
                        await self._set_state(WorkerState.ONLINE)
                        self._consecutive_failures = 0
                    else:
                        await self._set_state(WorkerState.OFFLINE)
            finally:
                self._reconnecting = False

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

        expired_keys = [
            key for key, (ts, _) in self._result_cache.items()
            if now - ts > ttl
        ]
        for key in expired_keys:
            del self._result_cache[key]

        expired_keys = [
            key for key, (ts, _) in self._receipt_cache.items()
            if now - ts > ttl
        ]
        for key in expired_keys:
            del self._receipt_cache[key]

    # ==================== 私有：后台任务取消 ====================

    async def _cancel_background_tasks(self) -> None:
        tasks = []
        for task in (
            self._task_subscriber,
            self._control_subscriber,
            self._status_pusher,
            self._log_pusher,
        ):
            if task is not None and not task.done():
                task.cancel()
                tasks.append(task)

        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        self._task_subscriber = None
        self._control_subscriber = None
        self._status_pusher = None
        self._log_pusher = None
