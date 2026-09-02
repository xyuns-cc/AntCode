"""Worker 端传输层抽象接口：Direct（内网直连 Redis Streams）与 Gateway（公网 gRPC/TLS）。"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from antcode_worker.domain.models import SourceBundle

# 代际错误与续期节拍各自独立成模块；此处显式再导出，保持既有导入路径不变。
from antcode_worker.transport.generation import GenerationLostError as GenerationLostError
from antcode_worker.transport.lease_cadence import ServerLeaseCadence


class TransportMode(StrEnum):
    DIRECT = "direct"  # 内网直连 Redis Streams
    GATEWAY = "gateway"  # 公网通过 Gateway gRPC


class WorkerState(StrEnum):
    WAITING = "waiting"  # 等待连接
    CONNECTING = "connecting"  # 正在连接
    REGISTERED = "registered"  # 已注册
    ONLINE = "online"  # 在线
    RECONNECTING = "reconnecting"  # 重连中
    OFFLINE = "offline"  # 离线


class ControlType(StrEnum):
    CANCEL = "cancel"
    KILL = "kill"
    CONFIG_UPDATE = "config_update"
    RUNTIME_MANAGE = "runtime_manage"


@dataclass
class ServerConfig:
    heartbeat_interval: int = 30
    reconnect_interval: int = 5
    max_reconnect_attempts: int = 10

    # Direct 模式配置
    redis_url: str = ""
    worker_queue_prefix: str = "worker:queue:"
    task_stream_prefix: str = "task:stream:"
    log_stream_prefix: str = "log:stream:"

    # Gateway 模式配置
    gateway_host: str = "localhost"
    gateway_port: int = 50051
    max_send_message_length: int = 50 * 1024 * 1024
    max_receive_message_length: int = 50 * 1024 * 1024


@dataclass(repr=False)
class TaskMessage:
    """任务消息

    环境变量等敏感字段在 ``__repr__`` / ``__str__`` 中会被脱敏，
    防止日志/异常堆栈意外打印明文 secret。
    """

    task_id: str
    project_id: str
    project_type: str = "code"
    priority: int = 0
    params: dict = field(default_factory=dict)
    environment: dict = field(default_factory=dict)
    timeout: int = 3600
    source_bundle: SourceBundle | None = None
    source_subdir: str = ""
    entry_point: str = ""
    runtime_env_name: str = ""
    run_id: str = ""
    created_at: datetime | None = None
    receipt: str | None = None

    def __repr__(self) -> str:
        from antcode_core.common.logging import sanitize_dict

        return (
            f"TaskMessage(task_id={self.task_id!r}, project_id={self.project_id!r}, "
            f"project_type={self.project_type!r}, priority={self.priority}, "
            f"params={sanitize_dict(self.params)!r}, "
            f"environment={sanitize_dict(self.environment)!r}, "
            f"timeout={self.timeout}, source_bundle={self.source_bundle!r}, "
            f"source_subdir={self.source_subdir!r}, entry_point={self.entry_point!r}, "
            f"runtime_env_name={self.runtime_env_name!r}, "
            f"run_id={self.run_id!r}, created_at={self.created_at!r}, "
            f"receipt={'***REDACTED***' if self.receipt else None})"
        )

    __str__ = __repr__


@dataclass
class TaskResult:
    run_id: str
    task_id: str
    status: str  # success, failed, cancelled, timeout
    exit_code: int | None = None  # None = 无进程退出码（还在跑/没跑起来）；0 = 真的退出 0
    error_message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float = 0
    data: dict = field(default_factory=dict)


@dataclass
class HeartbeatMessage:
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
    run_id: str
    log_type: str  # stdout, stderr
    content: str
    timestamp: datetime | None = None
    sequence: int = 0


@dataclass
class ControlMessage:
    control_type: str
    task_id: str = ""
    run_id: str = ""
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    receipt: str | None = None


class TransportBase(ServerLeaseCadence, ABC):
    """Worker 与 Gateway/Redis 通信的统一接口：两种模式对上层语义一致。"""

    def __init__(self, config: ServerConfig | None = None):
        self._config = config or ServerConfig()
        self._state = WorkerState.WAITING
        self._running = False

        self._on_task_dispatch: Callable | None = None
        self._on_task_cancel: Callable | None = None
        self._on_config_update: Callable | None = None
        self._on_state_change: Callable | None = None

    # ==================== 属性 ====================

    @property
    def state(self) -> WorkerState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return self._state == WorkerState.ONLINE

    @property
    @abstractmethod
    def mode(self) -> TransportMode:
        pass

    async def authoritative_now_ms(self) -> int:
        """运行时控制 deadline 时钟（P1-DR-04）：Direct 覆写为 Redis TIME；
        Gateway 权威过滤在中继侧，本地默认实现仅作粗粒度防线（本地时钟）。"""
        return int(datetime.now().timestamp() * 1000)

    # ==================== 生命周期 ====================

    @abstractmethod
    async def start(self) -> bool:
        pass

    @abstractmethod
    async def stop(self, grace_period: float = 5.0) -> None:
        pass

    async def deregister(self, reason: str = "shutdown") -> None:
        """主动通知 master 下线，让它立即判死而不必等 lease TTL 到期。

        默认 no-op；direct 实现 DEL lease key，gateway 实现发 RPC。
        """
        return

    # ==================== 任务操作 ====================

    @abstractmethod
    async def poll_task(self, timeout: float = 5.0) -> TaskMessage | None:
        """拉取任务，无任务返回 None。"""
        pass

    @abstractmethod
    async def ack_task(self, task_id: str, accepted: bool, reason: str = "") -> bool:
        """确认任务；``task_id`` 收任务回执或任务 ID 均可。"""
        pass

    @abstractmethod
    async def defer_task(self, receipt: str, reason: str = "") -> bool:
        """保留任务在 PEL，等待可见性超时后重新投递。

        ownership contention 不属于业务拒绝，不能 XADD 新消息、XACK 原消息，
        也不能累计普通 requeue 次数。
        """
        pass

    @abstractmethod
    async def requeue_task(self, receipt: str, reason: str = "") -> bool:
        pass

    @abstractmethod
    async def report_result(self, result: TaskResult) -> bool:
        pass

    @abstractmethod
    async def report_spider_data(
        self,
        run_id: str,
        items: list[dict[str, Any]],
    ) -> bool:
        """通过主进程 transport 上报一批 SpiderData item。"""
        pass

    @abstractmethod
    async def update_spider_meta(
        self,
        run_id: str,
        meta: dict[str, Any],
    ) -> bool:
        """通过主进程 transport 更新 SpiderData meta。"""
        pass

    # ==================== 日志操作 ====================

    @abstractmethod
    async def send_log(self, log: LogMessage) -> bool:
        pass

    @abstractmethod
    async def send_log_batch(self, logs: list[LogMessage]) -> bool:
        pass

    # ==================== 心跳 / Lease 操作 ====================

    @abstractmethod
    async def send_heartbeat(self, heartbeat: HeartbeatMessage) -> bool:
        """
        发送心跳（**deprecated alias for** ``lease_renew``）。

        P3 之后 ``lease_renew`` 是 canonical 名（liveness signal + lease
        元数据），``send_heartbeat`` 保留只为 ``HeartbeatReporter`` 现存调用
        点不破坏。新代码请直接调用 ``lease_renew``：

            new_lease_id, expires_at_ms, renew_after_ms, revoked = \\
                await transport.lease_renew(current_lease_id, metrics)

        ``send_heartbeat`` 的返回 ``bool`` 仅表示 RPC 是否成功，**不携带
        lease_id / 过期时间**。需要 lease 元数据请用 ``lease_renew``；
        服务端下发的租约时序走只读属性 ``lease_ttl_ms`` / ``lease_renew_after_ms``。

        Args:
            heartbeat: 心跳消息

        Returns:
            是否成功
        """
        pass

    async def lease_renew(
        self,
        current_lease_id: str,
        metrics: dict | None = None,
    ) -> tuple[str, int, int, bool]:
        """续租 / 首发租。

        新 P3 协议下的判活原语，返回一份完整的 lease 元数据。默认实现走旧
        ``send_heartbeat`` 桥接，没有 lease_id / 过期时间的子类仍可使用
        旧路径（兼容）；新驱动应覆盖本方法。

        Args:
            current_lease_id: Worker 当前持有的 ``lease_id``，空表示首次。
            metrics: 可选指标快照，落到 ``LeaseRequest.metrics``。

        Returns:
            ``(new_lease_id, expires_at_ms, renew_after_ms, revoked)``。
            兼容路径下 ``new_lease_id`` 为空、``expires_at_ms`` / ``renew_after_ms``
            为 0、``revoked`` 为 False。
        """
        # 默认兜底：把 metrics 适配为一份 HeartbeatMessage，调旧接口。
        heartbeat = HeartbeatMessage(
            worker_id="",
            status="online",
            cpu_percent=float((metrics or {}).get("cpu", 0.0) or 0.0),
            memory_percent=float((metrics or {}).get("memory", 0.0) or 0.0),
            disk_percent=float((metrics or {}).get("disk", 0.0) or 0.0),
            running_tasks=int((metrics or {}).get("running_tasks", 0) or 0),
            max_concurrent_tasks=int((metrics or {}).get("max_concurrent_tasks", 5) or 5),
        )
        ok = await self.send_heartbeat(heartbeat)
        return ("" if not ok else current_lease_id, 0, 0, False)

    async def claim_run_ownership(self, run_id: str, ttl_ms: int) -> bool:
        """Claim the cross-worker execution fence for one run."""
        raise RuntimeError(f"{self.mode} transport 不支持 run ownership claim")

    async def renew_run_ownership(self, run_id: str, ttl_ms: int) -> bool:
        """Renew a previously acquired execution fence."""
        raise RuntimeError(f"{self.mode} transport 不支持 run ownership renew")

    async def release_run_ownership(self, run_id: str) -> bool:
        """Release this Worker's execution fence for one run."""
        raise RuntimeError(f"{self.mode} transport 不支持 run ownership release")

    # ==================== 控制通道 ====================

    @abstractmethod
    async def poll_control(self, timeout: float = 5.0) -> ControlMessage | None:
        pass

    @abstractmethod
    async def ack_control(self, receipt: str) -> bool:
        pass

    @abstractmethod
    async def send_control_result(
        self,
        request_id: str,
        reply_stream: str,
        success: bool,
        *,
        receipt: str = "",
        data: Any = None,
        error: str = "",
    ) -> bool:
        """回传控制结果；``receipt`` 是原控制事件的回执。"""
        pass

    # ==================== 连接管理 ====================

    async def reconnect(self) -> bool:
        """触发重连（默认返回 False）"""
        return False

    # ==================== 回调注册 ====================

    def on_task_dispatch(self, callback: Callable) -> None:
        self._on_task_dispatch = callback

    def on_task_cancel(self, callback: Callable) -> None:
        self._on_task_cancel = callback

    def on_config_update(self, callback: Callable) -> None:
        self._on_config_update = callback

    def on_state_change(self, callback: Callable) -> None:
        self._on_state_change = callback

    # ==================== 状态管理 ====================

    async def _set_state(self, new_state: WorkerState) -> None:
        old_state = self._state
        if old_state == new_state:
            return

        self._state = new_state

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
        pass
