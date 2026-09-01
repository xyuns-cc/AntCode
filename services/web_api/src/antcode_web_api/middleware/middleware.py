"""中间件组件"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime
from typing import ClassVar

from antcode_core.common.security.network_source import extract_client_ip
from fastapi import HTTPException, Request, status
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

# ============================================================================
# 客户端 IP 解析（XFF 仅在受信代理白名单内才生效）
# ============================================================================


def get_client_ip(request: Request) -> str:
    """安全解析客户端 IP。

    只有当直接连进来的 socket 对端 IP 在 ``ANTCODE_TRUSTED_PROXIES`` 白名单内
    时才信任 ``X-Forwarded-For`` / ``X-Real-IP`` 头,否则一律以 socket 对端
    IP 为准,防止任意客户端通过伪造 XFF 头绕过基于 IP 的限流。
    """
    direct = request.client.host if request.client and request.client.host else ""
    if not direct:
        return "unknown"
    try:
        return extract_client_ip(
            direct,
            request.headers.get("X-Forwarded-For", ""),
            request.headers.get("X-Real-IP", ""),
            trusted_proxies=os.getenv("ANTCODE_TRUSTED_PROXIES", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ============================================================================
# 缓存失效命名空间映射（路由模块可扩展）
# ============================================================================

CACHE_NAMESPACE_MAP: dict[str, dict] = {
    "project": {
        "path": r"^/api/v1/projects",
        "id_pattern": r"^/api/v1/projects/(\w+)",
        "prefixes": ["project:list:"],
        "detail_prefix": "project:detail:{id}:",
    },
    "scheduler": {
        "path": r"^/api/v1/tasks",
        "id_pattern": r"^/api/v1/tasks/(\w+)",
        "prefixes": ["scheduler:list:", "scheduler:running:"],
        "detail_prefix": "scheduler:detail:{id}:",
    },
    "scheduler_runs": {
        "path": r"^/api/v1/runs",
        "prefixes": ["scheduler:list:", "scheduler:running:"],
    },
    "users": {
        "path": r"^/api/v1/users",
        "id_pattern": r"^/api/v1/users/(\w+)",
        "prefixes": ["user:list:"],
        "detail_prefix": "user:detail:{id}:",
    },
    "logs": {
        "path": r"^/api/v1/logs",
        "prefixes": [],
    },
    "dashboard": {
        "path": r"^/api/v1/dashboard",
        "prefixes": ["metrics:"],
    },
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """安全响应头中间件"""

    SECURITY_HEADERS: ClassVar[dict[str, str]] = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Content-Security-Policy": "default-src 'self'",
    }

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.update(self.SECURITY_HEADERS)
        return response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """X-Request-ID 中间件：读取或生成请求 ID"""

    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class _RequestBodyTooLarge(Exception):
    def __init__(self, limit: int, received: int):
        self.limit = limit
        self.received = received


class BodySizeMiddleware:
    """按 ASGI 分片累计实际请求体大小，覆盖 chunked 请求。"""

    def __init__(
        self,
        app,
        max_body_size: int = 10 * 1024 * 1024,
        max_upload_size: int | None = None,
    ):
        self.app = app
        self.max_body_size = max_body_size
        self.max_upload_size = max_upload_size if max_upload_size is not None else max_body_size

    @staticmethod
    def _header(scope: dict, name: bytes) -> str:
        for key, value in scope.get("headers", []):
            if key.lower() == name:
                return value.decode("latin-1")
        return ""

    def _error_response(self, status_code: int, message: str, data=None) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={
                "success": False,
                "code": status_code,
                "message": message,
                "data": data,
                "timestamp": datetime.now().isoformat(),
            },
        )

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        content_type = self._header(scope, b"content-type").lower()
        limit = self.max_upload_size if content_type.startswith("multipart/") else self.max_body_size
        content_length = self._header(scope, b"content-length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError:
                response = self._error_response(status.HTTP_400_BAD_REQUEST, "非法的 Content-Length")
                await response(scope, receive, send)
                return
            if declared_size < 0:
                response = self._error_response(status.HTTP_400_BAD_REQUEST, "非法的 Content-Length")
                await response(scope, receive, send)
                return
            if declared_size > limit:
                response = self._error_response(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    "请求体过大",
                    {"limit": limit, "received": declared_size},
                )
                await response(scope, receive, send)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _RequestBodyTooLarge(limit, received)
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge as exc:
            response = self._error_response(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "请求体过大",
                {"limit": exc.limit, "received": exc.received},
            )
            await response(scope, receive, send)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis 分布式滑动窗口限流中间件

    P2-05: 计数器实际存储在 Redis 的 Sorted Set 里 (见
    ``antcode_core.infrastructure.redis.rate_limiter``:
    Lua 脚本 ZREMRANGEBYSCORE + ZCARD + ZADD 原子完成滑动窗口判断),
    因此多个 web_api 副本会共享同一份 counter, **不会** 出现
    "副本数 × limit" 的漏洞。SERVER_WORKERS>1 或多容器副本上线
    也不需要额外协调。

    如果未来把 counter 拆回进程内内存(如加了本地 LRU 快路径),
    必须保留 Redis 兜底路径,并在多副本部署时把 limit / N 副本再传进来,
    否则 P2-05 会回归。
    """

    def __init__(self, app, calls: int = 100, period: int = 60):
        super().__init__(app)
        self.calls = calls
        self.period = period

    async def dispatch(self, request, call_next):
        try:
            client_ip = self._get_client_ip(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        # 分布式滑动窗口:所有副本落到同一个 Redis key, counter 全局共享。
        from antcode_core.infrastructure.redis.rate_limiter import redis_rate_limiter

        allowed = await redis_rate_limiter.is_allowed(client_ip, self.calls, self.period)
        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "success": False,
                    "code": status.HTTP_429_TOO_MANY_REQUESTS,
                    "message": "请求过于频繁",
                    "data": None,
                    "timestamp": datetime.now().isoformat(),
                },
            )

        return await call_next(request)

    def _get_client_ip(self, request) -> str:
        # 委托给模块级 get_client_ip:仅在 socket 对端 IP 命中受信代理白名单
        # 时才接受 XFF / X-Real-IP,防止外部客户端伪造 IP 绕过限流。
        return get_client_ip(request)


class CacheInvalidationMiddleware(BaseHTTPMiddleware):
    """写操作缓存失效中间件"""

    WRITE_METHODS: ClassVar[set[str]] = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(self, app):
        super().__init__(app)
        # 从配置字典构建编译后的匹配器
        self._path_patterns: list[tuple[re.Pattern, str]] = []
        self._id_patterns: dict[str, re.Pattern] = {}
        self._prefix_map: dict[str, list[str]] = {}
        self._detail_prefix_map: dict[str, str] = {}

        for ns, cfg in CACHE_NAMESPACE_MAP.items():
            compiled = re.compile(cfg["path"])
            self._path_patterns.append((compiled, ns))
            if "id_pattern" in cfg:
                self._id_patterns[ns] = re.compile(cfg["id_pattern"])
            self._prefix_map[ns] = cfg.get("prefixes", [])
            if "detail_prefix" in cfg:
                self._detail_prefix_map[ns] = cfg["detail_prefix"]

    def _match_namespace(self, path: str) -> str | None:
        for pattern, ns in self._path_patterns:
            if pattern.match(path):
                return ns
        return None

    async def dispatch(self, request, call_next):
        is_write = request.method in self.WRITE_METHODS
        response = await call_next(request)

        if is_write and response.status_code < 400:
            await self._invalidate_cache(request.url.path)

        return response

    async def _invalidate_cache(self, path: str):
        """根据路径清除相关缓存"""
        try:
            ns = self._match_namespace(path)
            if not ns:
                return

            prefixes = list(self._prefix_map.get(ns, []))

            # 提取资源 ID 并添加 detail 前缀
            if ns in self._id_patterns and ns in self._detail_prefix_map:
                if m := self._id_patterns[ns].match(path):
                    prefixes.append(self._detail_prefix_map[ns].format(id=m.group(1)))

            if prefixes:
                from antcode_core.infrastructure.cache import unified_cache

                for prefix in prefixes:
                    await unified_cache.clear_prefix(prefix)
                logger.debug(f"缓存已清除: {prefixes}")
        except Exception as e:
            logger.warning(f"缓存失效处理失败: {e}")


def make_middlewares():
    """创建 FastAPI 中间件列表"""
    from antcode_core.common.config import settings

    # T7-P2-3: PrometheusMiddleware 用惰性导入，避免 prometheus_client 未安装
    # 时启动挂掉；装了就自动采集。
    prometheus_middleware = None
    try:
        from antcode_web_api.prometheus_metrics import PrometheusMiddleware as _Prom

        prometheus_middleware = Middleware(_Prom)
    except ImportError:
        pass

    # P1-29 请求体总量兜底:
    # 默认 10MB JSON,复用 settings.MAX_FILE_SIZE 作为 multipart 上限,
    # 优先使用 settings.MAX_BODY_SIZE / MAX_UPLOAD_BODY_SIZE(若配置里加了)。
    default_body = 10 * 1024 * 1024
    max_body_size = int(getattr(settings, "MAX_BODY_SIZE", default_body) or default_body)
    max_upload_size = int(
        getattr(settings, "MAX_UPLOAD_BODY_SIZE", None)
        or getattr(settings, "MAX_FILE_SIZE", max_body_size)
        or max_body_size
    )

    middleware = [
        Middleware(RequestIDMiddleware),
        # 最外层业务前直接拒超大 body(P1-29),避免 rate limit / auth 已经
        # 消耗资源之后才发现 body 打爆内存。
        Middleware(
            BodySizeMiddleware,
            max_body_size=max_body_size,
            max_upload_size=max_upload_size,
        ),
        Middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
            allow_methods=settings.CORS_ALLOW_METHODS,
            allow_headers=settings.CORS_ALLOW_HEADERS,
        ),
        Middleware(SecurityHeadersMiddleware),
        Middleware(
            RateLimitMiddleware,
            calls=settings.RATE_LIMIT_CALLS,
            period=settings.RATE_LIMIT_PERIOD,
        ),
        Middleware(CacheInvalidationMiddleware),
    ]
    if prometheus_middleware is not None:
        # Starlette 的 build_middleware_stack() 对列表 reversed() 后逐层包裹,
        # 因此列表第一项才是最外层。放在 RequestID 之后、BodySize 之前:
        # 既保留 request_id 可用,又能让被 BodySize(413) / RateLimit(429) 短路的
        # 请求计入 antcode_http_requests_total——限流打满时指标不能失明。
        middleware.insert(1, prometheus_middleware)
    return middleware
