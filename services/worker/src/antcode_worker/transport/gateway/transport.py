"""
Gateway 传输层实现（Gateway 模式）

公网 Worker 通过 Gateway gRPC/TLS 连接。
实现 Worker-Initiated 连接模式，支持 TLS + auth。

Requirements: 5.5, 5.6, 5.7
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

    # 额外选项
    extra_options: dict[str, Any] = field(default_factory=dict)


class GatewayTransport(TransportBase):
    """
    Gateway 传输层实现

    公网 Worker 通过 Gateway gRPC/TLS 连接，提供：
    - Worker-Initiated 连接模式
    - TLS + mTLS/API key 认证
    - 自动重连与指数退避
    - Receipt 幂等性保证
    - 任务拉取、确认、结果上报
    - 日志发送、心跳上报

    Requirements: 5.5, 5.6, 5.7
    """

    def __init__(
        self,
        gateway_config: GatewayConfig | None = None,
        config: ServerConfig | None = None,
    ):
        super().__init__(config)
        self._gateway_config = gateway_config or GatewayConfig()

        # gRPC 组件
        self._channel: grpc_aio.Channel | None = None
        self._stub: Any = None
        self._stream: Any = None

        # 认证器
        self._authenticator: GatewayAuthenticator | None = None

        # 重连管理器
        self._reconnect_manager: ReconnectManager | None = None

        # 发送队列
        self._send_queue: asyncio.Queue | None = None

        # 接收任务
        self._receive_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None

        # 幂等性缓存
        self._receipt_cache: dict[str, tuple[float, Any]] = {}
        self._result_cache: dict[str, tuple[float, bool]] = {}

        # 连接状态
        self._connected = False
        self._last_heartbeat: datetime | None = None
        self._consecutive_failures = 0

        # 重连控制
        self._reconnecting = False  # 防止并发重连
        self._auth_failure_count = 0  # 认证失败计数
        self._max_auth_failures = 5  # 认证失败最大次数，超过后停止重连

    @property
    def mode(self) -> TransportMode:
        return TransportMode.GATEWAY

    @property
    def gateway_config(self) -> GatewayConfig:
        """获取 Gateway 配置"""
        return self._gateway_config

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._connected and self._channel is not None

    async def start(self) -> bool:
        """
        启动 Gateway 传输层

        建立 gRPC 连接，初始化认证和重连管理器。
        """
        if self._running:
            return True

        try:
            # 初始化认证器
            await self._init_authenticator()

            # 初始化重连管理器
            await self._init_reconnect_manager()

            # 建立连接
            success = await self._connect()
            if not success:
                logger.error("Gateway 初始连接失败")
                return False

            # 初始化发送队列
            self._send_queue = asyncio.Queue(maxsize=10000)

            self._running = True
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
        """
        停止 Gateway 传输层

        优雅关闭连接，清理资源。
        """
        if not self._running:
            return

        self._running = False

        # 取消后台任务
        await self._cancel_background_tasks()

        # 关闭连接
        await self._disconnect()

        # 清理缓存
        self._receipt_cache.clear()
        self._result_cache.clear()

        await self._set_state(WorkerState.OFFLINE)
        logger.info("Gateway 传输层已停止")

    async def poll_task(self, timeout: float = 5.0) -> TaskMessage | None:
        """
        从 Gateway 拉取任务

        通过 gRPC 调用 PollTask 方法。
        """
        if not self._stub or not self._running:
            return None

        try:
            # 导入 protobuf 消息
            from antcode_worker.transport.gateway.codecs import TaskDecoder

            # 构建请求
            request = self._build_poll_task_request(timeout)

            # 发送请求
            response = await asyncio.wait_for(
                self._stub.PollTask(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=timeout + 5,
            )

            # 解码响应
            if not response.has_task:
                return None

            task = TaskDecoder.decode(response.task)
            receipt_id = getattr(response, "receipt_id", "") or getattr(response, "message_id", "")
            if receipt_id:
                task.receipt = receipt_id
                self._receipt_cache[receipt_id] = (datetime.now().timestamp(), task.task_id)
            self._consecutive_failures = 0
            return task

        except TimeoutError:
            return None
        except Exception as e:
            self._consecutive_failures += 1
            logger.error(f"拉取任务失败: {e}")
            await self._handle_connection_error(e)
            return None

    async def ack_task(self, task_id: str, accepted: bool, reason: str = "") -> bool:
        """
        确认任务

        支持幂等性：相同 task_id 的重复确认会返回缓存结果。
        """
        if not self._stub or not self._running:
            return False

        receipt_id = task_id
        # 检查幂等性缓存
        cache_key = f"ack:{receipt_id}"
        if self._gateway_config.enable_receipt_idempotency:
            cached = self._get_cached_result(cache_key)
            if cached is not None:
                logger.debug(f"使用缓存的 ACK 结果: {receipt_id}")
                return cached

        try:
            # 构建请求
            request = self._build_ack_task_request(receipt_id, accepted, reason)

            # 发送请求
            response = await asyncio.wait_for(
                self._stub.AckTask(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )

            success = response.success

            # 缓存结果
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
        """重新入队任务（通过拒绝 ACK 实现）"""
        return await self.ack_task(receipt, accepted=False, reason=reason)

    async def report_result(self, result: TaskResult) -> bool:
        """
        上报任务结果

        支持幂等性：相同 task_id 的重复上报会返回缓存结果。
        """
        if not self._running:
            logger.warning(f"上报结果失败: 传输未运行 task_id={result.task_id}")
            return False
        if not self._stub:
            logger.warning(f"上报结果失败: gRPC stub 未就绪 task_id={result.task_id}")
            return False

        # 检查幂等性缓存
        cache_key = f"result:{result.task_id}"
        if self._gateway_config.enable_receipt_idempotency:
            cached = self._get_cached_result(cache_key)
            if cached is not None:
                logger.debug(f"使用缓存的结果上报: {result.task_id}")
                return cached

        try:
            from antcode_worker.transport.gateway.codecs import ResultEncoder

            # 构建请求
            request = ResultEncoder.encode(result, self._gateway_config.worker_id or "")

            # 发送请求
            response = await asyncio.wait_for(
                self._stub.ReportResult(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )

            success = response.success

            # 缓存结果
            if success and self._gateway_config.enable_receipt_idempotency:
                self._cache_result(cache_key, success)

            if not success:
                logger.warning(
                    "上报结果被拒绝: "
                    f"task_id={result.task_id}, run_id={result.run_id}, "
                    f"error={response.error}"
                )

            self._consecutive_failures = 0
            return success

        except Exception as e:
            self._consecutive_failures += 1
            logger.error(f"上报结果失败: {e}")
            await self._handle_connection_error(e)
            return False

    async def send_log(self, log: LogMessage) -> bool:
        """发送实时日志"""
        if not self._stub or not self._running:
            return False

        try:
            from antcode_worker.transport.gateway.codecs import LogEncoder

            # 构建请求
            request = LogEncoder.encode_realtime(log)

            # 发送请求
            response = await asyncio.wait_for(
                self._stub.SendLog(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )

            return response.success

        except Exception as e:
            logger.error(f"发送日志失败: {e}")
            await self._handle_connection_error(e)
            return False

    async def send_log_batch(self, logs: list[LogMessage]) -> bool:
        """发送批量日志"""
        if not self._stub or not self._running:
            return False

        try:
            from antcode_worker.transport.gateway.codecs import LogEncoder

            request = LogEncoder.encode_batch(logs)
            response = await asyncio.wait_for(
                self._stub.SendLogBatch(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )
            return response.success
        except Exception as e:
            logger.error(f"发送批量日志失败: {e}")
            await self._handle_connection_error(e)
            return False

    async def send_log_chunk(
        self,
        run_id: str,
        log_type: str,
        data: bytes,
        offset: int,
        is_final: bool = False,
    ) -> bool:
        """发送日志分片"""
        if not self._stub or not self._running:
            return False

        try:
            from antcode_worker.transport.gateway.codecs import LogEncoder

            # 构建请求
            request = LogEncoder.encode_chunk(
                run_id=run_id,
                log_type=log_type,
                data=data,
                offset=offset,
                is_final=is_final,
            )

            # 发送请求
            response = await asyncio.wait_for(
                self._stub.SendLogChunk(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )

            return response.success

        except Exception as e:
            logger.error(f"发送日志分片失败: {e}")
            await self._handle_connection_error(e)
            return False

    async def send_heartbeat(self, heartbeat: HeartbeatMessage) -> bool:
        """发送心跳"""
        if not self._stub or not self._running:
            return False

        try:
            from antcode_worker.transport.gateway.codecs import HeartbeatEncoder

            request = HeartbeatEncoder.encode(heartbeat)
            response = await asyncio.wait_for(
                self._stub.SendHeartbeat(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )

            if response.success:
                self._last_heartbeat = datetime.now()
                self._consecutive_failures = 0

            return response.success

        except Exception as e:
            self._consecutive_failures += 1
            logger.error(f"发送心跳失败: {e}")
            await self._handle_connection_error(e)
            return False

    async def poll_control(self, timeout: float = 5.0) -> Any:
        """拉取控制消息"""
        if not self._stub or not self._running:
            return None

        try:
            from antcode_worker.transport.gateway.codecs import ControlDecoder

            request = self._build_poll_control_request(timeout)
            response = await asyncio.wait_for(
                self._stub.PollControl(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=timeout + 5,
            )

            if not response.has_control:
                return None

            control = response.control
            control_type = control.WhichOneof("payload")
            if control_type == "task_cancel":
                data = ControlDecoder.decode_cancel(control.task_cancel)
                return ControlMessage(
                    control_type="cancel",
                    task_id=data.get("task_id", ""),
                    run_id=data.get("run_id", ""),
                    reason="",
                    receipt=getattr(response, "receipt_id", ""),
                )
            if control_type == "config_update":
                data = ControlDecoder.decode_config_update(control.config_update)
                return ControlMessage(
                    control_type="config_update",
                    payload=data,
                    receipt=getattr(response, "receipt_id", ""),
                )
            if control_type == "runtime_control":
                data = ControlDecoder.decode_runtime_control(control.runtime_control)
                return ControlMessage(
                    control_type="runtime_manage",
                    payload=data,
                    receipt=getattr(response, "receipt_id", ""),
                )
            return None
        except TimeoutError:
            return None
        except Exception as e:
            logger.error(f"拉取控制消息失败: {e}")
            await self._handle_connection_error(e)
            return None

    async def ack_control(self, receipt: str) -> bool:
        """确认控制消息"""
        if not self._stub or not self._running:
            return False

        try:
            request = self._build_ack_control_request(receipt)
            response = await asyncio.wait_for(
                self._stub.AckControl(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )
            return response.success
        except Exception as e:
            logger.error(f"确认控制消息失败: {e}")
            await self._handle_connection_error(e)
            return False

    async def send_control_result(
        self,
        request_id: str,
        reply_stream: str,
        success: bool,
        data: dict | None = None,
        error: str = "",
    ) -> bool:
        """回传控制结果"""
        if not self._stub or not self._running:
            return False

        try:
            from antcode_contracts import gateway_pb2

            payload_json = ""
            if data is not None:
                import json
                payload_json = json.dumps(data, ensure_ascii=False)

            request = gateway_pb2.ControlResultRequest(
                worker_id=self._gateway_config.worker_id or "",
                request_id=request_id,
                success=bool(success),
                payload_json=payload_json,
                error=error or "",
                reply_stream=reply_stream,
            )

            response = await asyncio.wait_for(
                self._stub.ReportControlResult(
                    request,
                    metadata=self._get_auth_metadata(),
                ),
                timeout=self._gateway_config.call_timeout,
            )
            return response.success
        except Exception as e:
            logger.error(f"回传控制结果失败: {e}")
            await self._handle_connection_error(e)
            return False

    def set_credentials(self, worker_id: str, api_key: str | None = None) -> None:
        """设置凭证"""
        self._gateway_config.worker_id = worker_id
        if api_key:
            self._gateway_config.api_key = api_key

    def get_status(self) -> dict[str, Any]:
        """获取传输层状态"""
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
            "last_heartbeat": (
                self._last_heartbeat.isoformat() if self._last_heartbeat else None
            ),
            "consecutive_failures": self._consecutive_failures,
            "reconnect_stats": reconnect_stats,
        }

    async def reconnect(self) -> bool:
        """触发重连"""
        await self._disconnect()
        return await self._connect()

    # ==================== 私有方法 ====================

    async def _init_authenticator(self) -> None:
        """初始化认证器"""
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
        """初始化重连管理器"""
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
        """建立 gRPC 连接"""
        target = f"{self._gateway_config.gateway_host}:{self._gateway_config.gateway_port}"

        try:
            from grpc import aio as grpc_aio

            # 构建 channel options
            options = [
                ("grpc.max_send_message_length", self._gateway_config.max_send_message_length),
                ("grpc.max_receive_message_length", self._gateway_config.max_receive_message_length),
                ("grpc.keepalive_time_ms", self._gateway_config.keepalive_time_ms),
                ("grpc.keepalive_timeout_ms", self._gateway_config.keepalive_timeout_ms),
                ("grpc.keepalive_permit_without_calls", self._gateway_config.keepalive_permit_without_calls),
            ]

            # 添加额外选项
            for key, value in self._gateway_config.extra_options.items():
                options.append((key, value))

            # 创建 channel
            if self._gateway_config.use_tls:
                credentials = self._create_tls_credentials()
                self._channel = grpc_aio.secure_channel(
                    target,
                    credentials,
                    options=options,
                )
            else:
                self._channel = grpc_aio.insecure_channel(
                    target,
                    options=options,
                )

            # 等待 channel 就绪
            await asyncio.wait_for(
                self._channel.channel_ready(),
                timeout=self._gateway_config.connect_timeout,
            )

            # 创建 stub
            self._stub = self._create_stub()

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
        """断开 gRPC 连接"""
        self._connected = False

        if self._channel:
            try:
                await self._channel.close()
            except Exception as e:
                logger.warning(f"关闭 channel 时出错: {e}")
            finally:
                self._channel = None

        self._stub = None
        self._stream = None

    def _create_tls_credentials(self) -> Any:
        """创建 TLS 凭证"""
        import grpc

        # 读取证书
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

    def _create_stub(self) -> Any:
        """创建 gRPC stub"""
        from antcode_contracts import gateway_pb2_grpc

        return gateway_pb2_grpc.GatewayServiceStub(self._channel)

    def _get_auth_metadata(self) -> list[tuple[str, str]]:
        """获取认证元数据"""
        if self._authenticator:
            return self._authenticator.get_metadata()
        return []

    def _build_poll_task_request(self, timeout: float) -> Any:
        """构建 PollTask 请求"""
        from antcode_contracts import gateway_pb2
        return gateway_pb2.PollTaskRequest(
            worker_id=self._gateway_config.worker_id or "",
            timeout_ms=int(timeout * 1000),
        )

    def _build_poll_control_request(self, timeout: float) -> Any:
        """构建 PollControl 请求"""
        from antcode_contracts import gateway_pb2
        return gateway_pb2.PollControlRequest(
            worker_id=self._gateway_config.worker_id or "",
            timeout_ms=int(timeout * 1000),
        )

    def _build_ack_control_request(self, receipt_id: str) -> Any:
        """构建 AckControl 请求"""
        from antcode_contracts import gateway_pb2
        return gateway_pb2.AckControlRequest(
            worker_id=self._gateway_config.worker_id or "",
            receipt_id=receipt_id,
        )

    def _build_ack_task_request(
        self, receipt_id: str, accepted: bool, reason: str
    ) -> Any:
        """构建 AckTask 请求"""
        task_id = self._get_receipt_task_id(receipt_id)
        from antcode_contracts import gateway_pb2
        return gateway_pb2.AckTaskRequest(
            task_id=task_id or "",
            receipt_id=receipt_id,
            worker_id=self._gateway_config.worker_id or "",
            accepted=accepted,
            reason=reason,
        )

    def _get_receipt_task_id(self, receipt_id: str) -> str:
        """根据 receipt 获取 task_id（用于日志与服务端校验）"""
        cached = self._receipt_cache.get(receipt_id)
        if not cached:
            return ""
        timestamp, task_id = cached
        now = datetime.now().timestamp()
        if now - timestamp > self._gateway_config.receipt_cache_ttl:
            self._receipt_cache.pop(receipt_id, None)
            return ""
        return str(task_id)

    async def _handle_connection_error(self, error: Exception) -> None:
        """处理连接错误

        改进：
        1. 检测认证错误，避免无限重试
        2. 使用退避逻辑
        3. 防止并发重连
        """
        import grpc

        # 检查是否为认证错误
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
                self._running = False  # 停止运行，避免继续轮询
                return
            # 认证错误使用较长的退避时间
            backoff = min(30.0, 5.0 * self._auth_failure_count)
            logger.warning(
                f"认证失败 ({self._auth_failure_count}/{self._max_auth_failures})，"
                f"{backoff:.1f}秒后重试。请检查 WORKER_API_KEY 配置"
            )
            await asyncio.sleep(backoff)
            return

        # 非认证错误，重置认证失败计数
        self._auth_failure_count = 0

        # 防止并发重连
        if self._reconnecting:
            logger.debug("重连进行中，跳过")
            return

        # 检查是否需要重连
        if self._consecutive_failures >= 3:
            self._reconnecting = True
            try:
                logger.warning(f"连续失败 {self._consecutive_failures} 次，尝试重连")
                await self._set_state(WorkerState.RECONNECTING)

                if self._reconnect_manager:
                    # 通知断开，触发带退避的重连循环
                    self._reconnect_manager.notify_disconnected(str(error))
                    # 等待重连完成
                    success = await self._reconnect_manager.wait_connected(timeout=120.0)
                    if success:
                        await self._set_state(WorkerState.ONLINE)
                        self._consecutive_failures = 0
                    else:
                        await self._set_state(WorkerState.OFFLINE)
                else:
                    # 没有重连管理器，使用简单退避重连
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

    def _get_cached_result(self, cache_key: str) -> bool | None:
        """获取缓存的结果"""
        if cache_key not in self._result_cache:
            return None

        timestamp, result = self._result_cache[cache_key]
        now = datetime.now().timestamp()

        if now - timestamp > self._gateway_config.receipt_cache_ttl:
            del self._result_cache[cache_key]
            return None

        return result

    def _cache_result(self, cache_key: str, result: bool) -> None:
        """缓存结果"""
        now = datetime.now().timestamp()
        self._result_cache[cache_key] = (now, result)

        # 清理过期缓存
        self._cleanup_cache()

    def _cleanup_cache(self) -> None:
        """清理过期缓存"""
        now = datetime.now().timestamp()
        ttl = self._gateway_config.receipt_cache_ttl

        # 清理 result 缓存
        expired_keys = [
            key for key, (ts, _) in self._result_cache.items()
            if now - ts > ttl
        ]
        for key in expired_keys:
            del self._result_cache[key]

        # 清理 receipt 缓存
        expired_keys = [
            key for key, (ts, _) in self._receipt_cache.items()
            if now - ts > ttl
        ]
        for key in expired_keys:
            del self._receipt_cache[key]

    async def _cancel_background_tasks(self) -> None:
        """取消后台任务"""
        tasks = []

        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            tasks.append(self._receive_task)

        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            tasks.append(self._heartbeat_task)

        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
