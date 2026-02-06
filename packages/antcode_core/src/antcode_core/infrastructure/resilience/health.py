"""
健康检查聚合模块

提供统一的健康检查端点，聚合所有服务组件的健康状态：
- 数据库连接
- Redis 连接（可选）
- 熔断器状态
- 内存/磁盘使用

使用示例:
    from antcode_core.infrastructure.resilience.health import health_checker

    # 获取完整健康状态
    status = await health_checker.check_all()

    # 注册自定义健康检查
    health_checker.register("custom_service", custom_check_func)
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from loguru import logger


class HealthStatus(str, Enum):
    """健康状态"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"  # 部分功能受限
    UNHEALTHY = "unhealthy"


@dataclass
class ComponentHealth:
    """组件健康状态"""

    name: str
    status: HealthStatus
    latency_ms: float
    message: str | None = None
    details: dict[str, Any] | None = None


@dataclass
class SystemHealth:
    """系统整体健康状态"""

    status: HealthStatus
    timestamp: float
    components: list[ComponentHealth]
    summary: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "timestamp": self.timestamp,
            "components": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "latency_ms": round(c.latency_ms, 2),
                    "message": c.message,
                    "details": c.details,
                }
                for c in self.components
            ],
            "summary": self.summary,
        }


class HealthChecker:
    """
    健康检查聚合器

    聚合所有服务组件的健康状态，提供统一的健康检查接口。
    """

    def __init__(self):
        self._checks: dict[str, Callable[[], Awaitable[ComponentHealth]]] = {}
        self._timeout = 5.0  # 单个检查超时时间

    def register(
        self,
        name: str,
        check_func: Callable[[], Awaitable[ComponentHealth]],
    ) -> None:
        """
        注册健康检查函数

        Args:
            name: 组件名称
            check_func: 异步检查函数，返回 ComponentHealth
        """
        self._checks[name] = check_func
        logger.debug(f"已注册健康检查: {name}")

    def unregister(self, name: str) -> None:
        """取消注册健康检查"""
        if name in self._checks:
            del self._checks[name]

    async def check_component(self, name: str) -> ComponentHealth:
        """检查单个组件"""
        if name not in self._checks:
            return ComponentHealth(
                name=name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=0,
                message="组件未注册",
            )

        start = time.time()
        try:
            result = await asyncio.wait_for(
                self._checks[name](),
                timeout=self._timeout,
            )
            return result
        except TimeoutError:
            return ComponentHealth(
                name=name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=(time.time() - start) * 1000,
                message=f"健康检查超时 ({self._timeout}s)",
            )
        except Exception as e:
            return ComponentHealth(
                name=name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=(time.time() - start) * 1000,
                message=str(e),
            )

    async def check_all(self) -> SystemHealth:
        """
        执行所有健康检查

        Returns:
            系统整体健康状态
        """
        # 并发执行所有检查
        tasks = [self.check_component(name) for name in self._checks]

        # 添加内置检查
        tasks.extend(
            [
                self._check_database(),
                self._check_redis(),
                self._check_circuit_breakers(),
                self._check_system_resources(),
                self._check_worker_availability(),
            ]
        )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        components: list[ComponentHealth] = []
        for result in results:
            if isinstance(result, ComponentHealth):
                components.append(result)
            elif isinstance(result, Exception):
                components.append(
                    ComponentHealth(
                        name="unknown",
                        status=HealthStatus.UNHEALTHY,
                        latency_ms=0,
                        message=str(result),
                    )
                )

        # 计算汇总
        summary = {
            "healthy": sum(1 for c in components if c.status == HealthStatus.HEALTHY),
            "degraded": sum(1 for c in components if c.status == HealthStatus.DEGRADED),
            "unhealthy": sum(1 for c in components if c.status == HealthStatus.UNHEALTHY),
        }

        # 确定整体状态
        if summary["unhealthy"] > 0:
            overall_status = HealthStatus.UNHEALTHY
        elif summary["degraded"] > 0:
            overall_status = HealthStatus.DEGRADED
        else:
            overall_status = HealthStatus.HEALTHY

        return SystemHealth(
            status=overall_status,
            timestamp=time.time(),
            components=components,
            summary=summary,
        )

    async def _check_database(self) -> ComponentHealth:
        """检查数据库连接"""
        start = time.time()
        try:
            from tortoise import Tortoise

            conn = Tortoise.get_connection("default")
            await conn.execute_query("SELECT 1")

            return ComponentHealth(
                name="database",
                status=HealthStatus.HEALTHY,
                latency_ms=(time.time() - start) * 1000,
                message="数据库连接正常",
            )
        except Exception as e:
            return ComponentHealth(
                name="database",
                status=HealthStatus.UNHEALTHY,
                latency_ms=(time.time() - start) * 1000,
                message=f"数据库连接失败: {e}",
            )

    async def _check_redis(self) -> ComponentHealth:
        """检查 Redis 连接"""
        start = time.time()
        try:
            from antcode_core.common.config import settings

            if not settings.REDIS_ENABLED:
                return ComponentHealth(
                    name="redis",
                    status=HealthStatus.HEALTHY,
                    latency_ms=0,
                    message="Redis 未启用（使用内存队列）",
                    details={"enabled": False},
                )

            import redis.asyncio as aioredis

            client = aioredis.from_url(
                settings.REDIS_URL,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
            )

            try:
                await client.ping()
                info = await client.info("server")

                return ComponentHealth(
                    name="redis",
                    status=HealthStatus.HEALTHY,
                    latency_ms=(time.time() - start) * 1000,
                    message="Redis 连接正常",
                    details={
                        "enabled": True,
                        "version": info.get("redis_version", "unknown"),
                    },
                )
            finally:
                await client.close()

        except ImportError:
            return ComponentHealth(
                name="redis",
                status=HealthStatus.DEGRADED,
                latency_ms=(time.time() - start) * 1000,
                message="redis 包未安装",
            )
        except Exception as e:
            return ComponentHealth(
                name="redis",
                status=HealthStatus.UNHEALTHY,
                latency_ms=(time.time() - start) * 1000,
                message=f"Redis 连接失败: {e}",
            )

    async def _check_circuit_breakers(self) -> ComponentHealth:
        """检查熔断器状态"""
        start = time.time()
        try:
            from antcode_core.infrastructure.resilience.circuit_breaker import CircuitBreaker, CircuitState

            breakers = CircuitBreaker.get_all()

            if not breakers:
                return ComponentHealth(
                    name="circuit_breakers",
                    status=HealthStatus.HEALTHY,
                    latency_ms=(time.time() - start) * 1000,
                    message="无熔断器注册",
                    details={"count": 0},
                )

            open_count = sum(1 for cb in breakers.values() if cb.state == CircuitState.OPEN)
            half_open_count = sum(
                1 for cb in breakers.values() if cb.state == CircuitState.HALF_OPEN
            )

            if open_count > 0:
                status = HealthStatus.DEGRADED
                message = f"{open_count} 个熔断器已打开"
            elif half_open_count > 0:
                status = HealthStatus.DEGRADED
                message = f"{half_open_count} 个熔断器处于半开状态"
            else:
                status = HealthStatus.HEALTHY
                message = "所有熔断器正常"

            return ComponentHealth(
                name="circuit_breakers",
                status=status,
                latency_ms=(time.time() - start) * 1000,
                message=message,
                details={
                    "total": len(breakers),
                    "open": open_count,
                    "half_open": half_open_count,
                    "closed": len(breakers) - open_count - half_open_count,
                    "breakers": {name: cb.state.value for name, cb in breakers.items()},
                },
            )
        except ImportError:
            return ComponentHealth(
                name="circuit_breakers",
                status=HealthStatus.HEALTHY,
                latency_ms=(time.time() - start) * 1000,
                message="熔断器模块未加载",
            )

    async def _check_system_resources(self) -> ComponentHealth:
        """检查系统资源"""
        start = time.time()
        try:
            import psutil

            # 内存使用
            memory = psutil.virtual_memory()
            memory_percent = memory.percent

            # 磁盘使用
            disk = psutil.disk_usage("/")
            disk_percent = disk.percent

            # CPU 使用
            cpu_percent = psutil.cpu_percent(interval=0.1)

            # 判断状态
            if memory_percent > 90 or disk_percent > 90:
                status = HealthStatus.UNHEALTHY
                message = "系统资源严重不足"
            elif memory_percent > 80 or disk_percent > 80:
                status = HealthStatus.DEGRADED
                message = "系统资源紧张"
            else:
                status = HealthStatus.HEALTHY
                message = "系统资源正常"

            return ComponentHealth(
                name="system_resources",
                status=status,
                latency_ms=(time.time() - start) * 1000,
                message=message,
                details={
                    "memory_percent": round(memory_percent, 1),
                    "memory_available_gb": round(memory.available / (1024**3), 2),
                    "disk_percent": round(disk_percent, 1),
                    "disk_free_gb": round(disk.free / (1024**3), 2),
                    "cpu_percent": round(cpu_percent, 1),
                },
            )
        except ImportError:
            return ComponentHealth(
                name="system_resources",
                status=HealthStatus.HEALTHY,
                latency_ms=(time.time() - start) * 1000,
                message="psutil 未安装，跳过资源检查",
            )
        except Exception as e:
            return ComponentHealth(
                name="system_resources",
                status=HealthStatus.DEGRADED,
                latency_ms=(time.time() - start) * 1000,
                message=f"资源检查失败: {e}",
            )

    async def _check_worker_availability(self) -> ComponentHealth:
        """检查 Worker 可用性"""
        start = time.time()
        try:
            from antcode_core.domain.models.enums import WorkerStatus
            from antcode_core.domain.models.worker import Worker

            online_workers = await Worker.filter(status=WorkerStatus.ONLINE.value).all()
            online_count = len(online_workers)
            total_workers = await Worker.all().count()

            if online_count == 0:
                return ComponentHealth(
                    name="workers",
                    status=HealthStatus.DEGRADED,
                    latency_ms=(time.time() - start) * 1000,
                    message="无可用 Worker",
                    details={
                        "online_count": 0,
                        "total_count": total_workers,
                    },
                )

            return ComponentHealth(
                name="workers",
                status=HealthStatus.HEALTHY,
                latency_ms=(time.time() - start) * 1000,
                message=f"{online_count} 个 Worker 在线",
                details={
                    "online_count": online_count,
                    "total_count": total_workers,
                    "online_workers": [
                        {"name": w.name, "host": w.host, "port": w.port} for w in online_workers[:10]
                    ],
                },
            )
        except Exception as e:
            return ComponentHealth(
                name="workers",
                status=HealthStatus.DEGRADED,
                latency_ms=(time.time() - start) * 1000,
                message=f"Worker 可用性检查失败: {e}",
            )

    async def liveness(self) -> bool:
        """
        存活检查（Kubernetes liveness probe）

        只检查应用是否存活，不检查依赖服务
        """
        return True

    async def readiness(self) -> bool:
        """
        就绪检查（Kubernetes readiness probe）

        检查应用是否准备好接收流量
        """
        try:
            # 检查数据库连接
            db_health = await self._check_database()
            return db_health.status != HealthStatus.UNHEALTHY
        except Exception:
            return False


# 全局健康检查器实例
health_checker = HealthChecker()
