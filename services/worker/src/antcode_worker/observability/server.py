"""
可观测性 HTTP 服务器

提供健康检查和 Prometheus 指标端点。

Requirements: 12.1, 12.2
"""

from typing import Any

from loguru import logger

try:
    from aiohttp import web
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    web = None

from antcode_worker.observability.health import HealthChecker, HealthStatus
from antcode_worker.observability.metrics import MetricsCollector


class ObservabilityServer:
    """
    可观测性 HTTP 服务器

    提供以下端点:
    - GET /health       - 基本健康检查
    - GET /health/live  - K8s 存活探针
    - GET /health/ready - K8s 就绪探针
    - GET /metrics      - Prometheus 指标

    Requirements: 12.1, 12.2
    """

    def __init__(
        self,
        health_checker: HealthChecker | None = None,
        metrics_collector: MetricsCollector | None = None,
    ):
        """
        初始化可观测性服务器

        Args:
            health_checker: 健康检查器实例
            metrics_collector: 指标收集器实例
        """
        self._health_checker = health_checker or HealthChecker()
        self._metrics_collector = metrics_collector or MetricsCollector()
        self._runner: Any | None = None
        self._site: Any | None = None
        self._host: str = "0.0.0.0"
        self._port: int = 8001

    @property
    def health_checker(self) -> HealthChecker:
        """获取健康检查器"""
        return self._health_checker

    @property
    def metrics_collector(self) -> MetricsCollector:
        """获取指标收集器"""
        return self._metrics_collector

    async def health(self, request: Any) -> Any:
        """
        基本健康检查端点

        GET /health

        Returns:
            JSON 响应: {"status": "ok"}
        """
        if not HAS_AIOHTTP:
            return None

        return web.json_response({
            "status": "ok",
            "service": "antcode-worker",
        })

    async def liveness(self, request: Any) -> Any:
        """
        K8s 存活探针端点

        GET /health/live

        检查进程是否存活，失败会触发 Pod 重启。

        Returns:
            JSON 响应: {"status": "healthy|unhealthy", "message": "..."}
        """
        if not HAS_AIOHTTP:
            return None

        result = self._health_checker.liveness()
        status_code = 200 if result.status == HealthStatus.HEALTHY else 503

        return web.json_response(
            {
                "status": result.status.value,
                "message": result.message,
            },
            status=status_code,
        )

    async def readiness(self, request: Any) -> Any:
        """
        K8s 就绪探针端点

        GET /health/ready

        检查服务是否可以接收流量。

        Returns:
            JSON 响应: {"status": "healthy|degraded|unhealthy", "message": "...", "details": {...}}
        """
        if not HAS_AIOHTTP:
            return None

        result = self._health_checker.readiness()
        status_code = 200 if result.status == HealthStatus.HEALTHY else 503

        return web.json_response(
            {
                "status": result.status.value,
                "message": result.message,
                "details": result.details,
            },
            status=status_code,
        )

    async def metrics(self, request: Any) -> Any:
        """
        Prometheus 指标端点

        GET /metrics

        Returns:
            Prometheus 格式的指标文本
        """
        if not HAS_AIOHTTP:
            return None

        prometheus_text = self._metrics_collector.to_prometheus()
        return web.Response(
            text=prometheus_text,
            content_type="text/plain",
        )

    async def start(self, host: str = "0.0.0.0", port: int = 8001) -> None:
        """
        启动 HTTP 服务器

        Args:
            host: 绑定地址
            port: 绑定端口
        """
        if not HAS_AIOHTTP:
            logger.warning("aiohttp 未安装，无法启动可观测性服务器")
            return

        self._host = host
        self._port = port

        app = web.Application()
        app.router.add_get("/health", self.health)
        app.router.add_get("/health/live", self.liveness)
        app.router.add_get("/health/ready", self.readiness)
        app.router.add_get("/metrics", self.metrics)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()

        logger.info(f"可观测性服务已启动: http://{host}:{port}")
        logger.info(f"  健康检查: http://{host}:{port}/health")
        logger.info(f"  存活探针: http://{host}:{port}/health/live")
        logger.info(f"  就绪探针: http://{host}:{port}/health/ready")
        logger.info(f"  Prometheus 指标: http://{host}:{port}/metrics")

    async def stop(self) -> None:
        """停止 HTTP 服务器"""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            logger.info("可观测性服务已停止")

    def set_ready(self, ready: bool) -> None:
        """设置就绪状态"""
        self._health_checker.set_ready(ready)

    def register_health_check(self, name: str, check: Any) -> None:
        """注册健康检查"""
        self._health_checker.register(name, check)
