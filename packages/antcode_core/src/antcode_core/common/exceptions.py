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


class AuthenticationError(AntCodeException):
    """认证错误异常"""

    def __init__(self, message: str = "认证失败"):
        super().__init__(message, error_code="AUTHENTICATION_ERROR")


class AuthorizationError(AntCodeException):
    """授权错误异常"""

    def __init__(self, message: str = "权限不足"):
        super().__init__(message, error_code="AUTHORIZATION_ERROR")


# =============================================================================
# 序列化异常
# =============================================================================


class SerializationError(AntCodeException):
    """序列化错误异常"""

    def __init__(self, message: str):
        super().__init__(message, error_code="SERIALIZATION_ERROR")


# =============================================================================
# 基础设施异常
# =============================================================================


class RedisConnectionError(AntCodeException):
    """Redis 连接错误"""

    def __init__(self, message: str = "Redis 连接失败"):
        super().__init__(message, error_code="REDIS_CONNECTION_ERROR")


# =============================================================================
# Worker 相关异常
# =============================================================================


class WorkerUnavailableError(AntCodeException):
    """Worker 不可用异常"""

    def __init__(self, message: str, worker_id: str | None = None):
        self.worker_id = worker_id
        super().__init__(message, error_code="WORKER_UNAVAILABLE")


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
