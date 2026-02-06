"""
压力测试配置和 fixtures
"""

import asyncio
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class LoadTestMetrics:
    """压力测试指标"""

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0

    @property
    def duration_seconds(self) -> float:
        """测试持续时间"""
        return self.end_time - self.start_time if self.end_time else time.time() - self.start_time

    @property
    def avg_latency_ms(self) -> float:
        """平均延迟"""
        return self.total_latency_ms / self.total_requests if self.total_requests else 0

    @property
    def requests_per_second(self) -> float:
        """每秒请求数"""
        return self.total_requests / self.duration_seconds if self.duration_seconds else 0

    @property
    def success_rate(self) -> float:
        """成功率"""
        return self.successful_requests / self.total_requests if self.total_requests else 0

    def record_request(self, success: bool, latency_ms: float) -> None:
        """记录请求"""
        self.total_requests += 1
        if success:
            self.successful_requests += 1
        else:
            self.failed_requests += 1
        self.total_latency_ms += latency_ms
        self.min_latency_ms = min(self.min_latency_ms, latency_ms)
        self.max_latency_ms = max(self.max_latency_ms, latency_ms)

    def finish(self) -> None:
        """完成测试"""
        self.end_time = time.time()

    def report(self) -> str:
        """生成报告"""
        return f"""
=== 压力测试报告 ===
总请求数: {self.total_requests}
成功请求: {self.successful_requests}
失败请求: {self.failed_requests}
成功率: {self.success_rate:.2%}
持续时间: {self.duration_seconds:.2f}s
QPS: {self.requests_per_second:.2f}
平均延迟: {self.avg_latency_ms:.2f}ms
最小延迟: {self.min_latency_ms:.2f}ms
最大延迟: {self.max_latency_ms:.2f}ms
"""


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """创建事件循环"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def load_test_metrics() -> LoadTestMetrics:
    """创建压力测试指标收集器"""
    return LoadTestMetrics()


@pytest.fixture
def load_test_config() -> dict[str, Any]:
    """压力测试配置"""
    return {
        "concurrent_users": 100,  # 并发用户数
        "requests_per_user": 100,  # 每用户请求数
        "ramp_up_seconds": 10,  # 预热时间
        "duration_seconds": 60,  # 测试持续时间
        "target_qps": 1000,  # 目标 QPS
        "max_latency_ms": 500,  # 最大可接受延迟
        "min_success_rate": 0.99,  # 最小成功率
    }
