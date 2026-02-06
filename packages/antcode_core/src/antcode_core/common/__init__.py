"""
Common 模块

通用功能：
- config: 配置管理
- logging: 日志配置
- exceptions: 异常定义
- ids: ID 生成
- time: 时间工具
- security: 安全相关（JWT、API Key、mTLS、权限）
"""

from antcode_core.common.config import settings
from antcode_core.common.exceptions import (
    AntCodeException,
    AuthenticationError,
    AuthorizationError,
    BatchNotFoundError,
    BatchStateError,
    BusinessError,
    ConfigurationError,
    CrawlError,
    DatabaseConnectionError,
    DuplicateUrlError,
    LeaderLostError,
    NotFoundError,
    QueueFullError,
    RedisConnectionError,
    SecurityError,
    SerializationError,
    StorageError,
    TaskError,
    TaskExecutionError,
    TaskNotFoundError,
    TaskTimeoutError,
    ValidationError,
    WorkerUnavailableError,
)
from antcode_core.common.ids import (
    generate_batch_id,
    generate_id,
    generate_run_id,
    generate_session_id,
    generate_short_id,
    generate_uuid,
    generate_worker_id,
)
from antcode_core.common.logging import get_logger, setup_logging
from antcode_core.common.time import (
    format_datetime,
    from_timestamp,
    local_to_utc,
    now_local,
    now_utc,
    parse_datetime,
    timestamp_ms,
    timestamp_sec,
    to_timestamp,
    utc_to_local,
)

__all__ = [
    # config
    "settings",
    # logging
    "setup_logging",
    "get_logger",
    # exceptions
    "AntCodeException",
    "AuthenticationError",
    "AuthorizationError",
    "BatchNotFoundError",
    "BatchStateError",
    "BusinessError",
    "ConfigurationError",
    "CrawlError",
    "DatabaseConnectionError",
    "DuplicateUrlError",
    "LeaderLostError",
    "NotFoundError",
    "QueueFullError",
    "RedisConnectionError",
    "SecurityError",
    "SerializationError",
    "StorageError",
    "TaskError",
    "TaskExecutionError",
    "TaskNotFoundError",
    "TaskTimeoutError",
    "ValidationError",
    "WorkerUnavailableError",
    # ids
    "generate_batch_id",
    "generate_id",
    "generate_run_id",
    "generate_session_id",
    "generate_short_id",
    "generate_uuid",
    "generate_worker_id",
    # time
    "format_datetime",
    "from_timestamp",
    "local_to_utc",
    "now_local",
    "now_utc",
    "parse_datetime",
    "timestamp_ms",
    "timestamp_sec",
    "to_timestamp",
    "utc_to_local",
]
