"""T7-P2-3: Prometheus /metrics 端点 + 中间件采集。

**为什么加这个**：R5 之前 metrics_service 只写 `monitor:stream:metrics` 自
有 Redis stream，没有 Prometheus 导出——运维接 Grafana 得自己写一段
stream→prom 转换。补一个标准 `/metrics` 端点让 Grafana / Prometheus 直连。

**指标覆盖**（第一批，够用为止；后续可按需扩展）：
- ``antcode_http_requests_total{method, path, status}`` Counter —— HTTP 请求
  总数
- ``antcode_http_request_duration_seconds{method, path}`` Histogram —— 请求
  延迟（默认 buckets）
- ``antcode_task_runs_total{status}`` Counter —— TaskRun 终态计数（由业务
  代码 inc）
- ``antcode_workers_online`` Gauge —— 在线 worker 数（scrape 时从 lease 拉）
- ``antcode_redispatch_pending`` Gauge —— 补派队列长度（scrape 时从 Redis
  ZSet 拉）

采集**惰性**：Gauge 在 scrape 时才拉 Redis，避免定时 poll 增加压力；
Counter/Histogram 由 middleware 实时 inc。

**路径 label 规范化**：`/api/v1/tasks/abc-123` 归一成
`/api/v1/tasks/{id}`——不然每个 run_id/project_id 一条时间序列会爆卡。
用 Starlette 的 route.path 做归一。
"""

from __future__ import annotations

import time

from antcode_core.common.security.auth import TokenData, get_current_admin_user
from fastapi import APIRouter, Depends, Request, Response
from loguru import logger
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "antcode_http_requests_total",
    "HTTP 请求总数",
    ["method", "path", "status"],
    registry=REGISTRY,
)

HTTP_REQUEST_DURATION = Histogram(
    "antcode_http_request_duration_seconds",
    "HTTP 请求延迟（秒）",
    ["method", "path"],
    # 覆盖 web API 常见延迟范围
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)

TASK_RUNS = Counter(
    "antcode_task_runs_total",
    "TaskRun 终态计数（由业务代码显式 inc）",
    ["status"],
    registry=REGISTRY,
)

WORKERS_ONLINE = Gauge(
    "antcode_workers_online",
    "当前在线 worker 数（scrape 时从 lease active 集合拉）",
    registry=REGISTRY,
)

REDISPATCH_PENDING = Gauge(
    "antcode_redispatch_pending",
    "补派队列长度（scrape 时从 task:redispatch ZSet 拉）",
    registry=REGISTRY,
)


def _normalize_path(request: Request) -> str:
    """把 `/api/v1/tasks/abc-123` 归一成 `/api/v1/tasks/{id}`。

    路径参数不归一会导致每条 run_id 一个时序，Prometheus 存储会爆卡。
    Starlette 的 route.path 已经是"路径模板"，直接用。
    """
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        return str(route.path)
    return request.url.path


async def _collect_workers_online() -> None:
    """从 lease active set 拉在线 worker 数量。"""
    try:
        from antcode_core.application.services.lease_service import LeaseStore
        from antcode_core.infrastructure.redis.client import get_redis_client
        from antcode_core.infrastructure.redis.control_plane import redis_namespace

        redis = await get_redis_client()
        store = LeaseStore(redis, namespace=redis_namespace())
        active = await store.list_active()
        WORKERS_ONLINE.set(len(active))
    except Exception as exc:
        logger.debug(f"scrape workers_online 失败（继续导出其它指标）: {exc}")


async def _collect_redispatch_pending() -> None:
    try:
        from antcode_core.application.services.scheduler.redispatch_service import (
            redispatch_service,
        )

        REDISPATCH_PENDING.set(await redispatch_service.pending_count())
    except Exception as exc:
        logger.debug(f"scrape redispatch_pending 失败: {exc}")


router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(_current_admin: TokenData = Depends(get_current_admin_user)) -> Response:
    """Prometheus scrape 入口。

    每次 scrape 时惰性刷新 Gauge，其余 Counter/Histogram 由中间件实时 inc。
    """
    await _collect_workers_online()
    await _collect_redispatch_pending()
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )


class PrometheusMiddleware:
    """ASGI middleware —— 采集所有 HTTP 请求的 method/path/status/duration。

    非阻塞：middleware 只做时间戳与 label 抽取，写指标是 O(1) 哈希访问。
    """

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        start = time.perf_counter()
        status_holder = {"code": 500}

        async def _send(message):
            if message["type"] == "http.response.start":
                status_holder["code"] = int(message.get("status", 0))
            await send(message)

        try:
            await self._app(scope, receive, _send)
        finally:
            duration = time.perf_counter() - start
            # /metrics 端点自身不计入指标，避免观察者效应
            path_raw = scope.get("path", "")
            if path_raw == "/metrics":
                return
            # 归一化 path：优先 route.path（模板），否则用原始 path
            request = Request(scope)
            path = _normalize_path(request)
            method = scope.get("method", "?")
            HTTP_REQUESTS.labels(method=method, path=path, status=str(status_holder["code"])).inc()
            HTTP_REQUEST_DURATION.labels(method=method, path=path).observe(duration)


def inc_task_run(status: str) -> None:
    """业务代码调这个把 TaskRun 终态计入。"""
    try:
        TASK_RUNS.labels(status=status).inc()
    except Exception:
        pass


__all__ = [
    "PrometheusMiddleware",
    "router",
    "inc_task_run",
    "TASK_RUNS",
    "HTTP_REQUESTS",
    "HTTP_REQUEST_DURATION",
]
