"""
Gateway 服务配置模块

从环境变量读取 gRPC 相关配置。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from antcode_core.common.log_limits import (
    DEFAULT_LOG_MAX_BATCH_BYTES,
    DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES,
    LogBatchLimits,
    positive_env_int,
)

from antcode_gateway.tls_expiry import (
    DEFAULT_TLS_EXPIRY_CHECK_INTERVAL_SECONDS,
    DEFAULT_TLS_EXPIRY_WARNING_DAYS,
    SECONDS_PER_DAY,
    TlsExpiryPolicy,
)

# 在读 env 前先加载 .env（GatewayConfig 用 os.getenv 直连 os.environ，无此步骤会
# 读不到项目根 .env）。dotenv 缺失时静默跳过，不影响 CI/CD 已通过 env 注入的场景。
try:
    from dotenv import load_dotenv

    _env_file = Path(__file__).resolve().parents[4] / ".env"
    if _env_file.exists():
        load_dotenv(_env_file, override=False)
except ImportError:
    pass


def _default_grpc_workers() -> int:
    """计算 gRPC 服务器默认工作线程数

    根据 CPU 核数动态调整：cpu * 4，封顶 32。
    可通过环境变量 ``GRPC_MAX_WORKERS`` 覆盖。

    参考值：
    - 2 核机器: 8
    - 4 核机器: 16
    - 8 核及以上: 32（封顶）
    """
    env_value = os.getenv("GRPC_MAX_WORKERS")
    if env_value:
        return int(env_value)
    return min(32, (os.cpu_count() or 4) * 4)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"缺少必需环境变量: {name}")
    return value


@dataclass
class GatewayConfig:
    """Gateway 服务器配置

    配置项从环境变量读取，支持通过 .env 文件配置。
    """

    # 服务器地址
    host: str = field(default_factory=lambda: os.getenv("GRPC_HOST", "0.0.0.0"))

    # 服务器端口
    port: int = field(default_factory=lambda: int(os.getenv("GRPC_PORT", "50051")))

    # 最大工作线程数（默认 min(32, cpu_count * 4)，可通过 GRPC_MAX_WORKERS 覆盖）
    max_workers: int = field(default_factory=_default_grpc_workers)

    # 是否启用 gRPC 服务
    enabled: bool = field(default_factory=lambda: os.getenv("GRPC_ENABLED", "true").lower() == "true")

    # 最大发送消息大小 (50MB)
    max_send_message_length: int = 50 * 1024 * 1024

    # 最大接收消息大小 (50MB)
    max_receive_message_length: int = 50 * 1024 * 1024

    # StreamLogs 业务层实际 protobuf/content 字节上限
    log_max_batch_bytes: int = field(
        default_factory=lambda: positive_env_int("GATEWAY_LOG_MAX_BATCH_BYTES", DEFAULT_LOG_MAX_BATCH_BYTES)
    )
    log_max_entry_content_bytes: int = field(
        default_factory=lambda: positive_env_int(
            "GATEWAY_LOG_MAX_ENTRY_CONTENT_BYTES",
            DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES,
        )
    )

    # 心跳保活时间（毫秒）
    keepalive_time_ms: int = 30000

    # 心跳保活超时（毫秒）
    keepalive_timeout_ms: int = 10000

    # 允许无调用时发送 keepalive ping
    keepalive_permit_without_calls: bool = True

    # 心跳间隔 (秒)
    heartbeat_interval: int = field(default_factory=lambda: int(os.getenv("GRPC_HEARTBEAT_INTERVAL", "30")))

    # 心跳超时 (秒) - 超过此时间未收到心跳则标记节点离线
    heartbeat_timeout: int = field(default_factory=lambda: int(os.getenv("GRPC_HEARTBEAT_TIMEOUT", "90")))

    # 优雅关闭等待时间 (秒)
    shutdown_grace_period: float = field(default_factory=lambda: float(os.getenv("GRPC_SHUTDOWN_GRACE_PERIOD", "5.0")))

    # TLS 配置 (可选)
    tls_cert_path: str | None = field(default_factory=lambda: os.getenv("GRPC_TLS_CERT_PATH") or None)
    tls_key_path: str | None = field(default_factory=lambda: os.getenv("GRPC_TLS_KEY_PATH") or None)
    tls_ca_path: str | None = field(default_factory=lambda: os.getenv("GRPC_TLS_CA_PATH") or None)

    # TLS 材料到期监控（见 tls_expiry）：热更新只解决"换不了"，提前量靠这两个阈值
    tls_expiry_warning_days: int = field(
        default_factory=lambda: positive_env_int("GRPC_TLS_EXPIRY_WARNING_DAYS", DEFAULT_TLS_EXPIRY_WARNING_DAYS)
    )
    tls_expiry_check_interval_seconds: int = field(
        default_factory=lambda: positive_env_int(
            "GRPC_TLS_EXPIRY_CHECK_INTERVAL_SECONDS",
            DEFAULT_TLS_EXPIRY_CHECK_INTERVAL_SECONDS,
        )
    )

    # 认证配置
    auth_enabled: bool = field(default_factory=lambda: os.getenv("AUTH_ENABLED", "true").lower() == "true")
    # 显式允许在启用鉴权的情况下用明文端口启动（仅本地/测试）。
    # 生产必须走 TLS/mTLS；把 api_key 或 JWT 放在明文 gRPC 上等于把凭证挂到公网。
    allow_insecure_with_auth: bool = field(
        default_factory=lambda: os.getenv("ANTCODE_GATEWAY_ALLOW_INSECURE", "false").lower() == "true"
    )

    # 限流配置
    rate_limit_enabled: bool = field(default_factory=lambda: os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true")
    rate_limit_rate: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_RATE", "100")))
    rate_limit_capacity: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_CAPACITY", "200")))

    # Redis 配置（用于 Streams 读取）
    redis_url: str = field(default_factory=lambda: _required_env("REDIS_URL"))

    def __post_init__(self) -> None:
        LogBatchLimits(
            max_batch_bytes=self.log_max_batch_bytes,
            max_entry_content_bytes=self.log_max_entry_content_bytes,
        )

    @property
    def server_options(self) -> list[tuple]:
        """获取 gRPC 服务器选项"""
        return [
            ("grpc.max_send_message_length", self.max_send_message_length),
            ("grpc.max_receive_message_length", self.max_receive_message_length),
            ("grpc.keepalive_time_ms", self.keepalive_time_ms),
            ("grpc.keepalive_timeout_ms", self.keepalive_timeout_ms),
            (
                "grpc.keepalive_permit_without_calls",
                self.keepalive_permit_without_calls,
            ),
            ("grpc.http2.min_recv_ping_interval_without_data_ms", 10000),
            ("grpc.http2.max_pings_without_data", 0),
            # P1-#17: 限制每个 HTTP/2 连接最大并发 stream 数, 避免 worker
            # 端无界 multiplex 把 gateway 资源打满。
            ("grpc.max_concurrent_streams", 1000),
            # P2-04: max_connection_idle_ms=0 在 grpc-python 里会被内部
            # clamp 成很小的窗口, 导致 grpc_health_probe 这类短连接
            # (打开→Check→关闭, 全程亚秒) 被判 idle 直接 GOAWAY, 触发
            # probe 侧偶发 "connection closed" 假 unhealthy。
            #
            # 修法: 给一个明显大于 grpc_health_probe / 常规探针 RTT 的值
            # (5 分钟), 让 idle 检测存在但不会误杀短连接; 长连接的
            # 保活/心跳仍由 keepalive_time_ms / keepalive_timeout_ms 处理。
            # max_connection_age_ms 不显式设置, 沿用 grpc 默认 INT_MAX
            # (禁用主动老化), 避免把 worker↔gateway 的长连接周期性砍断。
            #
            # 长连接不老化 ⇒ 证书材料的变化只对**新连接**生效
            # (``tls_material.create_reloadable_server_credentials``)。
            # 切断在途会话不靠连接老化, 靠控制面的 Worker 生命周期围栏:
            # ``lease_service.disable_worker`` 装 Redis fence,
            # ``LeaseStore.is_current`` 在每条数据面消息上校验, 停用即时生效。
            # 想靠 max_connection_age_ms 做"定期强制重新握手"之前, 先确认
            # 上面这条路径为什么不够 —— 它比周期性砍长连接便宜得多。
            ("grpc.max_connection_idle_ms", 300_000),
        ]

    @property
    def tls_enabled(self) -> bool:
        """是否启用 TLS"""
        return bool(self.tls_cert_path and self.tls_key_path)

    @property
    def mtls_enabled(self) -> bool:
        """是否启用 mTLS（双向 TLS）"""
        return self.tls_enabled and bool(self.tls_ca_path)

    @property
    def tls_expiry_policy(self) -> TlsExpiryPolicy:
        """把"天"换算成秒的唯一一处，避免调用方各写各的 86400。"""
        return TlsExpiryPolicy(
            warning_seconds=self.tls_expiry_warning_days * SECONDS_PER_DAY,
            interval_seconds=self.tls_expiry_check_interval_seconds,
        )

    @property
    def listen_address(self) -> str:
        """获取监听地址"""
        return f"{self.host}:{self.port}"


# 全局配置实例
gateway_config = GatewayConfig()
