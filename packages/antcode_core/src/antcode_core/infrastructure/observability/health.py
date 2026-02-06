"""健康检查模块

提供服务健康检查功能。
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class HealthStatus(str, Enum):
    """健康状态"""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """健康检查结果"""

    name: str
    status: HealthStatus
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass
class OverallHealth:
    """整体健康状态"""

    status: HealthStatus
    checks: list[HealthCheckResult]
    checked_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "checks": [c.to_dict() for c in self.checks],
            "checked_at": self.checked_at.isoformat(),
        }


# 健康检查函数类型
HealthCheckFunc = Callable[[], Coroutine[Any, Any, HealthCheckResult]]


class HealthChecker:
    """健康检查器"""

    def __init__(self):
        self._checks: dict[str, HealthCheckFunc] = {}

    def register(self, name: str, check_func: HealthCheckFunc) -> None:
        """注册健康检查函数

        Args:
            name: 检查名称
            check_func: 异步检查函数
        """
        self._checks[name] = check_func

    def unregister(self, name: str) -> None:
        """取消注册健康检查函数"""
        self._checks.pop(name, None)

    async def check(self, name: str) -> HealthCheckResult:
        """执行单个健康检查

        Args:
            name: 检查名称

        Returns:
            健康检查结果
        """
        if name not in self._checks:
            return HealthCheckResult(
                name=name,
                status=HealthStatus.UNKNOWN,
                message=f"未知的健康检查: {name}",
            )

        try:
            return await self._checks[name]()
        except Exception as e:
            return HealthCheckResult(
                name=name,
                status=HealthStatus.UNHEALTHY,
                message=f"检查失败: {str(e)}",
            )

    async def check_all(self, timeout: float = 5.0) -> OverallHealth:
        """执行所有健康检查

        Args:
            timeout: 超时时间（秒）

        Returns:
            整体健康状态
        """
        results: list[HealthCheckResult] = []

        for name in self._checks:
            try:
                result = await asyncio.wait_for(self.check(name), timeout=timeout)
                results.append(result)
            except TimeoutError:
                results.append(
                    HealthCheckResult(
                        name=name,
                        status=HealthStatus.UNHEALTHY,
                        message="检查超时",
                    )
                )

        # 计算整体状态
        if not results:
            overall_status = HealthStatus.UNKNOWN
        elif all(r.status == HealthStatus.HEALTHY for r in results):
            overall_status = HealthStatus.HEALTHY
        elif any(r.status == HealthStatus.UNHEALTHY for r in results):
            overall_status = HealthStatus.UNHEALTHY
        else:
            overall_status = HealthStatus.DEGRADED

        return OverallHealth(status=overall_status, checks=results)


# 全局健康检查器
health_checker = HealthChecker()


# 内置健康检查函数
async def check_redis() -> HealthCheckResult:
    """检查 Redis 连接"""
    try:
        from antcode_core.infrastructure.redis.client import RedisConnectionPool

        pool = await RedisConnectionPool.get_instance()
        is_connected = await pool.is_connected()

        if is_connected:
            stats = await pool.get_pool_stats()
            return HealthCheckResult(
                name="redis",
                status=HealthStatus.HEALTHY,
                message="Redis 连接正常",
                details=stats,
            )
        else:
            return HealthCheckResult(
                name="redis",
                status=HealthStatus.UNHEALTHY,
                message="Redis 连接断开",
            )
    except Exception as e:
        return HealthCheckResult(
            name="redis",
            status=HealthStatus.UNHEALTHY,
            message=f"Redis 检查失败: {str(e)}",
        )


async def check_database() -> HealthCheckResult:
    """检查数据库连接"""
    try:
        from tortoise import Tortoise

        # 尝试执行简单查询
        conn = Tortoise.get_connection("default")
        await conn.execute_query("SELECT 1")

        return HealthCheckResult(
            name="database",
            status=HealthStatus.HEALTHY,
            message="数据库连接正常",
        )
    except Exception as e:
        return HealthCheckResult(
            name="database",
            status=HealthStatus.UNHEALTHY,
            message=f"数据库检查失败: {str(e)}",
        )


def register_default_checks() -> None:
    """注册默认的健康检查"""
    health_checker.register("redis", check_redis)
    health_checker.register("database", check_database)
