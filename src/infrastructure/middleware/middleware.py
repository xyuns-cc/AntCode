"""中间件组件"""
import re
import time
from collections import defaultdict
from typing import ClassVar

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger

from src.core.security.auth import jwt_auth


class AdminPermissionMiddleware(BaseHTTPMiddleware):
    """管理员权限验证中间件"""

    # 预编译正则表达式
    ADMIN_PATTERNS: ClassVar[list] = [
        re.compile(r'^/api/v1/users/?$'),
        re.compile(r'^/api/v1/users/\d+/?$'),
        re.compile(r'^/api/v1/users/\d+/reset-password/?$'),
        re.compile(r'^/api/v1/users/cache/?.*$'),
    ]

    EXCLUDED_PATTERNS: ClassVar[list] = [
        (re.compile(r'^/api/v1/users/\d+/?$'), 'GET'),
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
        from src.services.users.user_service import user_service

        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少认证信息")

        try:
            token = auth_header.split(' ', 1)[1]
            token_data = jwt_auth.verify_token(token)
            user = await user_service.get_user_by_id(token_data.user_id)

            if not user or not user.is_admin:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"权限验证失败: {e}")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证失败")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """安全响应头中间件"""

    SECURITY_HEADERS: ClassVar[dict] = {
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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """速率限制中间件（内存实现，多进程部署需改用 Redis）"""

    def __init__(self, app, calls: int = 100, period: int = 60):
        super().__init__(app)
        self.calls = calls
        self.period = period
        self.requests: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.time()
        self._cleanup_interval = 60

    async def dispatch(self, request, call_next):
        client_ip = self._get_client_ip(request)
        current_time = time.time()

        # 定期清理
        if current_time - self._last_cleanup > self._cleanup_interval:
            self._cleanup_expired_records(current_time)
            self._last_cleanup = current_time

        if self._is_rate_limited(client_ip, current_time):
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"success": False, "code": 429, "message": "请求过于频繁"}
            )

        self.requests[client_ip].append(current_time)
        return await call_next(request)

    def _get_client_ip(self, request) -> str:
        if forwarded := request.headers.get('X-Forwarded-For'):
            return forwarded.split(',')[0].strip()
        if real_ip := request.headers.get('X-Real-IP'):
            return real_ip
        return request.client.host if request.client else 'unknown'

    def _cleanup_expired_records(self, current_time: float):
        cutoff = current_time - self.period
        expired = [ip for ip, ts in self.requests.items() if not any(t > cutoff for t in ts)]
        for ip in expired:
            del self.requests[ip]
        for ip in self.requests:
            self.requests[ip] = [t for t in self.requests[ip] if t > cutoff]

    def _is_rate_limited(self, client_ip: str, current_time: float) -> bool:
        if client_ip not in self.requests:
            return False
        cutoff = current_time - self.period
        return sum(1 for t in self.requests[client_ip] if t > cutoff) >= self.calls


class CacheInvalidationMiddleware(BaseHTTPMiddleware):
    """写操作缓存失效中间件"""

    WRITE_METHODS: ClassVar[set] = {"POST", "PUT", "PATCH", "DELETE"}

    # 预编译路径匹配
    PATH_PATTERNS: ClassVar[list] = [
        (re.compile(r"^/api/v1/projects"), "project"),
        (re.compile(r"^/api/v1/scheduler"), "scheduler"),
        (re.compile(r"^/api/v1/envs"), "envs"),
        (re.compile(r"^/api/v1/users"), "users"),
        (re.compile(r"^/api/v1/logs"), "logs"),
        (re.compile(r"^/api/v1/dashboard"), "dashboard"),
    ]

    ID_PATTERNS: ClassVar[dict] = {
        "project": re.compile(r"^/api/v1/projects/(\w+)"),
        "scheduler": re.compile(r"^/api/v1/scheduler/tasks/(\w+)"),
        "envs": re.compile(r"^/api/v1/envs/venvs/(\w+)"),
        "users": re.compile(r"^/api/v1/users/(\w+)"),
    }

    def _match_namespace(self, path: str) -> str | None:
        for pattern, ns in self.PATH_PATTERNS:
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

            prefixes = self._get_cache_prefixes(ns, path)
            if prefixes:
                from src.infrastructure.cache import unified_cache
                for prefix in prefixes:
                    await unified_cache.clear_prefix(prefix)
                logger.debug(f"缓存已清除: {prefixes}")
        except Exception as e:
            logger.warning(f"缓存失效处理失败: {e}")

    def _get_cache_prefixes(self, ns: str, path: str) -> list[str]:
        """获取需要清除的缓存前缀"""
        prefixes = []

        if ns == "project":
            prefixes.append("project:list:")
            if m := self.ID_PATTERNS["project"].match(path):
                prefixes.append(f"project:detail:{m.group(1)}:")

        elif ns == "scheduler":
            prefixes.extend(["scheduler:list:", "scheduler:running:"])
            if m := self.ID_PATTERNS["scheduler"].match(path):
                prefixes.append(f"scheduler:detail:{m.group(1)}:")

        elif ns == "envs":
            prefixes.append("envs:list:")
            if m := self.ID_PATTERNS["envs"].match(path):
                prefixes.append(f"envs:packages:{m.group(1)}:")
            if "interpreters" in path or "versions" in path:
                prefixes.extend(["envs:interpreters:", "envs:versions:"])

        elif ns == "users":
            prefixes.append("user:list:")
            if m := self.ID_PATTERNS["users"].match(path):
                prefixes.append(f"user:detail:{m.group(1)}:")

        elif ns == "dashboard":
            prefixes.append("metrics:")

        return prefixes


def make_middlewares():
    """Create middleware list for FastAPI application.

    Returns:
        list: List of Middleware instances.
    """
    from fastapi.middleware import Middleware
    from fastapi.middleware.cors import CORSMiddleware

    from src.core.config import settings

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
            allow_methods=settings.CORS_ALLOW_METHODS,
            allow_headers=settings.CORS_ALLOW_HEADERS,
        ),
        Middleware(SecurityHeadersMiddleware),
        Middleware(RateLimitMiddleware, calls=settings.RATE_LIMIT_CALLS, period=settings.RATE_LIMIT_PERIOD),
        Middleware(AdminPermissionMiddleware),
        Middleware(CacheInvalidationMiddleware),
    ]
    return middleware
