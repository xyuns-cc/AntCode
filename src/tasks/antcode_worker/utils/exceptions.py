"""
Worker 自定义异常模块

定义 Worker 节点使用的异常类，独立于 Master。
"""


class SerializationError(Exception):
    """序列化错误异常"""
    pass


class SecurityError(Exception):
    """安全检查异常"""
    pass


class TaskExecutionError(Exception):
    """任务执行错误"""
    def __init__(self, message: str, task_id: str = None):
        self.message = message
        self.task_id = task_id
        super().__init__(message)


class ResourceLimitError(Exception):
    """资源限制错误"""
    pass


class ConnectionError(Exception):
    """连接错误"""
    pass
