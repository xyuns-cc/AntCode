"""
Worker 域错误定义

Requirements: 3.7
"""

from typing import Any


class WorkerError(Exception):
    """Worker 基础错误"""

    def __init__(
        self,
        message: str,
        code: str = "WORKER_ERROR",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class ExecutionError(WorkerError):
    """执行错误"""

    def __init__(
        self,
        message: str,
        run_id: str | None = None,
        exit_code: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="EXECUTION_ERROR", details=details)
        self.run_id = run_id
        self.exit_code = exit_code


class TransportError(WorkerError):
    """传输层错误"""

    def __init__(
        self,
        message: str,
        operation: str | None = None,
        retryable: bool = True,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="TRANSPORT_ERROR", details=details)
        self.operation = operation
        self.retryable = retryable


class RuntimeError(WorkerError):
    """运行时环境错误"""

    def __init__(
        self,
        message: str,
        runtime_hash: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="RUNTIME_ERROR", details=details)
        self.runtime_hash = runtime_hash


class PluginError(WorkerError):
    """插件错误"""

    def __init__(
        self,
        message: str,
        plugin_name: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="PLUGIN_ERROR", details=details)
        self.plugin_name = plugin_name


class ConfigError(WorkerError):
    """配置错误"""

    def __init__(
        self,
        message: str,
        config_key: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="CONFIG_ERROR", details=details)
        self.config_key = config_key


class TimeoutError(ExecutionError):
    """超时错误"""

    def __init__(
        self,
        message: str,
        run_id: str | None = None,
        timeout_seconds: float | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, run_id=run_id, exit_code=124, details=details)
        self.code = "TIMEOUT_ERROR"
        self.timeout_seconds = timeout_seconds


class CancellationError(ExecutionError):
    """取消错误"""

    def __init__(
        self,
        message: str,
        run_id: str | None = None,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, run_id=run_id, exit_code=-15, details=details)
        self.code = "CANCELLATION_ERROR"
        self.reason = reason


class ResourceLimitError(ExecutionError):
    """资源限制错误"""

    def __init__(
        self,
        message: str,
        run_id: str | None = None,
        resource_type: str | None = None,  # memory, cpu, disk
        limit: float | None = None,
        actual: float | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, run_id=run_id, details=details)
        self.code = "RESOURCE_LIMIT_ERROR"
        self.resource_type = resource_type
        self.limit = limit
        self.actual = actual
