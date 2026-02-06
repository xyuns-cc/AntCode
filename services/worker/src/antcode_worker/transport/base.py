"""
传输层抽象基类

定义 Worker 端传输层的抽象接口。
支持两种模式：
- Direct 模式：内网直连 Redis Streams
- Gateway 模式：公网通过 Gateway gRPC/TLS 连接

Requirements: 7.2, 11.3
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TransportMode(str, Enum):
    """传输模式枚举"""
    DIRECT = "direct"    # 内网直连 Redis Streams
    GATEWAY = "gateway"  # 公网通过 Gateway gRPC


class WorkerState(str, Enum):
    """Worker 状态枚举"""
    WAITING = "waiting"          # 等待连接
    CONNECTING = "connecting"    # 正在连接
    REGISTERED = "registered"    # 已注册
    ONLINE = "online"            # 在线
    RECONNECTING = "reconnecting"  # 重连中
    OFFLINE = "offline"          # 离线


class ControlType(str, Enum):
    """控制消息类型"""
    CANCEL = "cancel"
    KILL = "kill"
    CONFIG_UPDATE = "config_update"
    RUNTIME_MANAGE = "runtime_manage"


@dataclass
class ServerConfig:
    """传输层配置"""
    # 通用配置
    heartbeat_interval: int = 30
    reconnect_interval: int = 5
    max_reconnect_attempts: int = 10

    # Direct 模式配置
    redis_url: str = "redis://localhost:6379/0"
    worker_queue_prefix: str = "worker:queue:"
    task_stream_prefix: str = "task:stream:"
    log_stream_prefix: str = "log:stream:"

    # Gateway 模式配置
    gateway_host: str = "localhost"
    gateway_port: int = 50051
    max_send_message_length: int = 50 * 1024 * 1024
    max_receive_message_length: int = 50 * 1024 * 1024


@dataclass
class TaskMessage:
    """任务消息"""
    task_id: str
    project_id: str
    project_type: str = "code"
    priority: int = 0
    params: dict = field(default_factory=dict)
    environment: dict = field(default_factory=dict)
    timeout: int = 3600
    download_url: str = ""
    file_hash: str = ""
    entry_point: str = ""
    is_compressed: bool | None = None  # None 表示未指定，由 fetcher 自动判断
    run_id: str = ""
    created_at: datetime | None = None
    receipt: str | None = None


@dataclass
class TaskResult:
    """任务结果"""
    run_id: str
    task_id: str
    status: str  # success, failed, cancelled, timeout
    exit_code: int = 0
    error_message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float = 0
    data: dict = field(default_factory=dict)


@dataclass
class HeartbeatMessage:
    """心跳消息"""
    worker_id: str
    status: str = "online"
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    disk_percent: float = 0.0
    running_tasks: int = 0
    max_concurrent_tasks: int = 5
    version: str = ""
    timestamp: datetime | None = None


@dataclass
class LogMessage:
    """日志消息"""
    execution_id: str
    log_type: str  # stdout, stderr
    content: str
    timestamp: datetime | None = None
    sequence: int = 0


@dataclass
class ControlMessage:
    """控制消息"""
    control_type: str
    task_id: str = ""
    run_id: str = ""
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    receipt: str | None = None


class TransportBase(ABC):
    """
    传输层抽象基类

    定义 Worker 与 Gateway/Redis 之间通信的统一接口。
    无论使用 Direct 模式还是 Gateway 模式，都提供一致的语义。

    Requirements: 7.2, 11.3
    """

    def __init__(self, config: ServerConfig | None = None):
        self._config = config or ServerConfig()
        self._state = WorkerState.WAITING
        self._running = False

        # 回调函数
        self._on_task_dispatch: Callable | None = None
        self._on_task_cancel: Callable | None = None
        self._on_config_update: Callable | None = None
        self._on_state_change: Callable | None = None

    # ==================== 属性 ====================

    @property
    def state(self) -> WorkerState:
        """当前状态"""
        return self._state

    @property
    def is_running(self) -> bool:
        """是否运行中"""
        return self._running

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._state == WorkerState.ONLINE

    @property
    @abstractmethod
    def mode(self) -> TransportMode:
        """传输模式"""
        pass

    # ==================== 生命周期 ====================

    @abstractmethod
    async def start(self) -> bool:
        """
        启动传输层

        Returns:
            是否启动成功
        """
        pass

    @abstractmethod
    async def stop(self, grace_period: float = 5.0) -> None:
        """
        停止传输层

        Args:
            grace_period: 优雅关闭等待时间（秒）
        """
        pass

    # ==================== 任务操作 ====================

    @abstractmethod
    async def poll_task(self, timeout: float = 5.0) -> TaskMessage | None:
        """
        拉取任务

        Args:
            timeout: 超时时间（秒）

        Returns:
            任务消息，无任务返回 None
        """
        pass

    @abstractmethod
    async def ack_task(self, task_id: str, accepted: bool, reason: str = "") -> bool:
        """
        确认任务

        Args:
            task_id: 任务回执或任务 ID
            accepted: 是否接受
            reason: 拒绝原因

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    async def requeue_task(self, receipt: str, reason: str = "") -> bool:
        """
        重新入队任务

        Args:
            receipt: 任务回执
            reason: 重新入队原因

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    async def report_result(self, result: TaskResult) -> bool:
        """
        上报任务结果

        Args:
            result: 任务结果

        Returns:
            是否成功
        """
        pass

    # ==================== 日志操作 ====================

    @abstractmethod
    async def send_log(self, log: LogMessage) -> bool:
        """
        发送日志

        Args:
            log: 日志消息

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    async def send_log_batch(self, logs: list[LogMessage]) -> bool:
        """
        批量发送日志

        Args:
            logs: 日志列表

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    async def send_log_chunk(
        self,
        execution_id: str,
        log_type: str,
        data: bytes,
        offset: int,
        is_final: bool = False,
    ) -> bool:
        """
        发送日志分片（存储通道）

        Args:
            execution_id: 执行 ID
            log_type: 日志类型 (stdout/stderr)
            data: 日志数据
            offset: 偏移量
            is_final: 是否最后一片

        Returns:
            是否成功
        """
        pass

    # ==================== 心跳操作 ====================

    @abstractmethod
    async def send_heartbeat(self, heartbeat: HeartbeatMessage) -> bool:
        """
        发送心跳

        Args:
            heartbeat: 心跳消息

        Returns:
            是否成功
        """
        pass

    # ==================== 控制通道 ====================

    @abstractmethod
    async def poll_control(self, timeout: float = 5.0) -> ControlMessage | None:
        """
        拉取控制消息

        Args:
            timeout: 超时时间（秒）

        Returns:
            控制消息或 None
        """
        pass

    @abstractmethod
    async def ack_control(self, receipt: str) -> bool:
        """
        确认控制消息

        Args:
            receipt: 控制消息回执

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    async def send_control_result(
        self,
        request_id: str,
        reply_stream: str,
        success: bool,
        data: dict | None = None,
        error: str = "",
    ) -> bool:
        """
        回传控制结果

        Args:
            request_id: 请求标识
            reply_stream: 回执 Stream
            success: 是否成功
            data: 结果数据
            error: 错误信息

        Returns:
            是否成功
        """
        pass

    # ==================== 连接管理 ====================

    async def reconnect(self) -> bool:
        """触发重连（默认返回 False）"""
        return False

    # ==================== 回调注册 ====================

    def on_task_dispatch(self, callback: Callable) -> None:
        """注册任务分发回调"""
        self._on_task_dispatch = callback

    def on_task_cancel(self, callback: Callable) -> None:
        """注册任务取消回调"""
        self._on_task_cancel = callback

    def on_config_update(self, callback: Callable) -> None:
        """注册配置更新回调"""
        self._on_config_update = callback

    def on_state_change(self, callback: Callable) -> None:
        """注册状态变更回调"""
        self._on_state_change = callback

    # ==================== 状态管理 ====================

    async def _set_state(self, new_state: WorkerState) -> None:
        """
        设置状态

        Args:
            new_state: 新状态
        """
        old_state = self._state
        if old_state == new_state:
            return

        self._state = new_state

        # 触发状态变更回调
        if self._on_state_change:
            try:
                import asyncio
                result = self._on_state_change(old_state, new_state)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

    # ==================== 状态查询 ====================

    @abstractmethod
    def get_status(self) -> dict[str, Any]:
        """
        获取传输层状态

        Returns:
            状态信息字典
        """
        pass
