"""
AntCode 异常模块

仅包含与 HTTP 无关的异常定义。
"""

from __future__ import annotations

# =============================================================================
# 基础异常类
# =============================================================================


class AntCodeException(Exception):
    """AntCode 异常基类"""

    def __init__(self, message: str, error_code: str | None = None):
        self.message = message
        self.error_code = error_code
        super().__init__(message)


class ConfigurationError(AntCodeException):
    """配置错误异常"""

    def __init__(self, message: str):
        super().__init__(message, error_code="CONFIGURATION_ERROR")


class ValidationError(AntCodeException):
    """验证错误异常"""

    def __init__(self, message: str, field: str | None = None):
        self.field = field
        super().__init__(message, error_code="VALIDATION_ERROR")


class NotFoundError(AntCodeException):
    """资源不存在异常"""

    def __init__(self, resource: str, identifier: str | int | None = None):
        self.resource = resource
        self.identifier = identifier
        message = f"{resource} 不存在"
        if identifier:
            message = f"{resource} {identifier} 不存在"
        super().__init__(message, error_code="NOT_FOUND")


class AuthenticationError(AntCodeException):
    """认证错误异常"""

    def __init__(self, message: str = "认证失败"):
        super().__init__(message, error_code="AUTHENTICATION_ERROR")


class AuthorizationError(AntCodeException):
    """授权错误异常"""

    def __init__(self, message: str = "权限不足"):
        super().__init__(message, error_code="AUTHORIZATION_ERROR")


class BusinessError(AntCodeException):
    """业务错误异常（非 HTTP 异常）"""

    def __init__(self, message: str, error_code: str | None = None):
        super().__init__(message, error_code=error_code)


# =============================================================================
# 序列化与安全异常
# =============================================================================


class SerializationError(AntCodeException):
    """序列化错误异常"""

    def __init__(self, message: str):
        super().__init__(message, error_code="SERIALIZATION_ERROR")


class SecurityError(AntCodeException):
    """安全检查异常"""

    def __init__(self, message: str):
        super().__init__(message, error_code="SECURITY_ERROR")


# =============================================================================
# 基础设施异常
# =============================================================================


class RedisConnectionError(AntCodeException):
    """Redis 连接错误"""

    def __init__(self, message: str = "Redis 连接失败"):
        super().__init__(message, error_code="REDIS_CONNECTION_ERROR")


class DatabaseConnectionError(AntCodeException):
    """数据库连接错误"""

    def __init__(self, message: str = "数据库连接失败"):
        super().__init__(message, error_code="DATABASE_CONNECTION_ERROR")


class StorageError(AntCodeException):
    """存储错误"""

    def __init__(self, message: str):
        super().__init__(message, error_code="STORAGE_ERROR")


# =============================================================================
# Worker 相关异常
# =============================================================================


class WorkerUnavailableError(AntCodeException):
    """Worker 不可用异常"""

    def __init__(self, message: str, worker_id: str | None = None):
        self.worker_id = worker_id
        super().__init__(message, error_code="WORKER_UNAVAILABLE")


# =============================================================================
# 任务相关异常
# =============================================================================


class TaskError(AntCodeException):
    """任务错误基类"""


class TaskNotFoundError(TaskError):
    """任务不存在"""

    def __init__(self, task_id: str | int):
        self.task_id = task_id
        super().__init__(f"任务 {task_id} 不存在", error_code="TASK_NOT_FOUND")


class TaskTimeoutError(TaskError):
    """任务超时"""

    def __init__(self, task_id: str | int, timeout_seconds: int):
        self.task_id = task_id
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"任务 {task_id} 超时，超时时间 {timeout_seconds} 秒",
            error_code="TASK_TIMEOUT",
        )


class TaskExecutionError(TaskError):
    """任务执行错误"""

    def __init__(self, message: str, task_id: str | int | None = None):
        self.task_id = task_id
        full_message = f"任务执行失败: {message}"
        if task_id:
            full_message += f" (task_id: {task_id})"
        super().__init__(full_message, error_code="TASK_EXECUTION_ERROR")


# =============================================================================
# 爬取相关异常
# =============================================================================


class CrawlError(AntCodeException):
    """爬取错误基类"""


class BatchNotFoundError(CrawlError):
    """批次不存在"""

    def __init__(self, batch_id: str | int):
        self.batch_id = batch_id
        super().__init__(f"批次 {batch_id} 不存在", error_code="BATCH_NOT_FOUND")


class BatchStateError(CrawlError):
    """批次状态错误"""

    def __init__(self, batch_id: str | int, current_state: str, expected_states: list[str]):
        self.batch_id = batch_id
        self.current_state = current_state
        self.expected_states = expected_states
        super().__init__(
            f"批次 {batch_id} 状态错误: 当前 {current_state}, 期望 {expected_states}",
            error_code="BATCH_STATE_ERROR",
        )


class QueueFullError(CrawlError):
    """队列已满"""

    def __init__(self, queue_name: str, max_size: int):
        self.queue_name = queue_name
        self.max_size = max_size
        super().__init__(
            f"队列 {queue_name} 已满，最大容量 {max_size}",
            error_code="QUEUE_FULL",
        )


class DuplicateUrlError(CrawlError):
    """URL 重复"""

    def __init__(self, url: str):
        self.url = url
        super().__init__(f"URL 已存在: {url}", error_code="DUPLICATE_URL")


class LeaderLostError(CrawlError):
    """Leader 角色丢失"""

    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        super().__init__(f"Worker {worker_id} 失去 Leader 角色", error_code="LEADER_LOST")
