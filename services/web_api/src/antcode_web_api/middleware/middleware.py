"""中间件组件"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime
from re import Pattern
from typing import Any, ClassVar

from antcode_core.common.logging import sanitize_dict
from antcode_core.common.security.auth import jwt_auth
from fastapi import HTTPException, Request, status
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

# ============================================================================
# 客户端 IP 解析（XFF 仅在受信代理白名单内才生效）
# ============================================================================


def _trusted_proxies() -> set[str]:
    """返回受信反向代理 IP 列表（来自环境变量 ANTCODE_TRUSTED_PROXIES）。

    每次调用即时读取以方便在 runtime 通过 settings 重载;若需要更高频访问可由
    调用方自行缓存。空字符串、空白条目都会被剔除。
    """
    raw = os.getenv("ANTCODE_TRUSTED_PROXIES", "") or ""
    return {ip.strip() for ip in raw.split(",") if ip.strip()}


def get_client_ip(request: Request) -> str:
    """安全解析客户端 IP。

    只有当直接连进来的 socket 对端 IP 在 ``ANTCODE_TRUSTED_PROXIES`` 白名单内
    时才信任 ``X-Forwarded-For`` / ``X-Real-IP`` 头,否则一律以 socket 对端
    IP 为准,防止任意客户端通过伪造 XFF 头绕过基于 IP 的限流。
    """
    direct = request.client.host if request.client else ""
    if not direct:
        return "unknown"

    trusted = _trusted_proxies()
    if direct not in trusted:
        return direct

    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        # 取左侧最初一跳的原始客户端 IP
        first = xff.split(",")[0].strip()
        if first:
            return first
    real_ip = request.headers.get("X-Real-IP", "")
    if real_ip:
        return real_ip.strip()
    return direct


def safe_sanitize_body(body: Any) -> Any:
    """对将要写入访问日志的请求/响应 body 做敏感字段脱敏。

    复用 ``antcode_core.common.logging.sanitize_dict``,对常见的 ``api_key``、
    ``password``、``token`` 等键值做 ``***REDACTED***`` 替换;非 dict 直接返回。
    """
    if isinstance(body, dict):
        return sanitize_dict(body)
    return body


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


class AdminPermissionMiddleware(BaseHTTPMiddleware):
    """管理员权限验证中间件"""

    # 预编译正则表达式
    ADMIN_PATTERNS: ClassVar[list[Pattern[str]]] = [
        re.compile(r"^/api/v1/users/?$"),
        re.compile(r"^/api/v1/users/[^/]+/?$"),
        re.compile(r"^/api/v1/users/[^/]+/reset-password/?$"),
        re.compile(r"^/api/v1/users/cache/?.*$"),
    ]

    EXCLUDED_PATTERNS: ClassVar[list[tuple[Pattern[str], str]]] = [
        (re.compile(r"^/api/v1/users/[^/]+/?$"), "GET"),
    ]

    async def dispatch(self, request, call_next):
        path = request.url.path
        method = request.method

        is_admin_path = any(p.match(path) for p in self.ADMIN_PATTERNS)

        if is_admin_path:
            is_excluded = any(p.match(path) and method == m for p, m in self.EXCLUDED_PATTERNS)
            if not is_excluded:
                await self._verify_admin_permission(request)

        return await call_next(request)

    async def _verify_admin_permission(self, request):
        """验证管理员权限"""
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少认证信息")

        try:
            token = auth_header.split(" ", 1)[1]
            token_data = jwt_auth.verify_token(token)

            if not token_data.is_admin:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"权限验证失败: {e}")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证失败")


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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis 分布式滑动窗口限流中间件"""

    def __init__(self, app, calls: int = 100, period: int = 60):
        super().__init__(app)
        self.calls = calls
        self.period = period

    async def dispatch(self, request, call_next):
        client_ip = self._get_client_ip(request)

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
                    prefixes.append(
                        self._detail_prefix_map[ns].format(id=m.group(1))
                    )

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

    middleware = [
        Middleware(RequestIDMiddleware),
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
        Middleware(AdminPermissionMiddleware),
        Middleware(CacheInvalidationMiddleware),
    ]
    if prometheus_middleware is not None:
        # 放最外层：先记时再进业务，让 5xx 也能被采集
        middleware.append(prometheus_middleware)
    return middleware
