"""
策略模块

实现 retry/timeout/resource 策略。

Requirements: 4.4
"""

from dataclasses import dataclass, field


@dataclass
class RetryPolicy:
    """重试策略"""
    max_retries: int = 3                  # 最大重试次数
    retry_delay: float = 1.0              # 重试延迟（秒）
    exponential_backoff: bool = True      # 是否指数退避
    max_delay: float = 60.0               # 最大延迟（秒）

    def get_delay(self, attempt: int) -> float:
        """计算重试延迟"""
        if not self.exponential_backoff:
            return self.retry_delay
        delay = self.retry_delay * (2 ** attempt)
        return min(delay, self.max_delay)

    def should_retry(self, attempt: int, error: Exception | None = None) -> bool:
        """是否应该重试"""
        # 可以根据错误类型决定是否重试
        return attempt < self.max_retries


@dataclass
class TimeoutPolicy:
    """超时策略"""
    execution_timeout: int = 3600         # 执行超时（秒）
    grace_period: int = 10                # 优雅关闭等待（秒）
    poll_timeout: float = 5.0             # 轮询超时（秒）
    ack_timeout: float = 10.0             # ACK 超时（秒）


@dataclass
class ResourcePolicy:
    """资源策略"""
    max_concurrent: int = 5               # 最大并发
    memory_limit_mb: int = 0              # 内存限制（MB，0=不限制）
    cpu_limit_seconds: int = 0            # CPU 时间限制（秒，0=不限制）
    disk_limit_mb: int = 0                # 磁盘限制（MB，0=不限制）


@dataclass
class Policies:
    """策略集合"""
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    timeout: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    resource: ResourcePolicy = field(default_factory=ResourcePolicy)


def default_policies() -> Policies:
    """获取默认策略"""
    return Policies()
