"""中间件组件"""
import re
import time

from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.auth import jwt_auth
from src.core.cache import unified_cache
from src.services.users.user_service import user_service


class AdminPermissionMiddleware(BaseHTTPMiddleware):
    """管理员权限验证中间件"""
    
    ADMIN_PATHS = [
        r'^/api/v1/users/?$',
        r'^/api/v1/users/\d+/?$',
        r'^/api/v1/users/\d+/reset-password/?$',
        r'^/api/v1/users/cache/?.*$',
    ]
    
    EXCLUDED_PATHS = [
        (r'^/api/v1/users/\d+/?$', 'GET'),
    ]

    async def dispatch(self, request, call_next):
        path = request.url.path
        method = request.method
        
        is_admin_path = any(re.match(pattern, path) for pattern in self.ADMIN_PATHS)
        
        if is_admin_path:
            is_excluded = any(
                re.match(pattern, path) and method == excluded_method
                for pattern, excluded_method in self.EXCLUDED_PATHS
            )
            
            if not is_excluded:
                await self._verify_admin_permission(request)
        
        response = await call_next(request)
        return response

    async def _verify_admin_permission(self, request):
        try:
            auth_header = request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing authentication"
                )
            
            token = auth_header.split(' ')[1]
            token_data = jwt_auth.verify_token(token)
            
            user = await user_service.get_user_by_id(token_data.user_id)
            if not user or not user.is_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin privileges required"
                )
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"权限验证失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Permission verification failed"
            )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """安全头中间件"""
    
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """速率限制中间件"""
    
    def __init__(self, app, calls=100, period=60):
        super().__init__(app)
        self.calls = calls
        self.period = period
        self.requests = {}
    
    async def dispatch(self, request, call_next):
        client_ip = self._get_client_ip(request)
        current_time = time.time()
        
        self._cleanup_expired_records(current_time)
        
        if self._is_rate_limited(client_ip, current_time):
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded"}
            )
        
        self._record_request(client_ip, current_time)
        
        return await call_next(request)
    
    def _get_client_ip(self, request):
        forwarded_for = request.headers.get('X-Forwarded-For')
        if forwarded_for:
            return forwarded_for.split(',')[0].strip()
        
        real_ip = request.headers.get('X-Real-IP')
        if real_ip:
            return real_ip
        
        return request.client.host if request.client else 'unknown'
    
    def _cleanup_expired_records(self, current_time):
        expired_ips = []
        for ip, timestamps in self.requests.items():
            self.requests[ip] = [
                t for t in timestamps 
                if current_time - t < self.period
            ]
            if not self.requests[ip]:
                expired_ips.append(ip)
        
        for ip in expired_ips:
            del self.requests[ip]
    
    def _is_rate_limited(self, client_ip, current_time):
        if client_ip not in self.requests:
            return False
        
        recent_requests = [
            t for t in self.requests[client_ip]
            if current_time - t < self.period
        ]
        
        return len(recent_requests) >= self.calls
    
    def _record_request(self, client_ip, current_time):
        if client_ip not in self.requests:
            self.requests[client_ip] = []
        
        self.requests[client_ip].append(current_time)


class CacheInvalidationMiddleware(BaseHTTPMiddleware):
    """写操作缓存失效中间件"""

    WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    PATH_NAMESPACE = [
        (r"^/api/v1/projects", "project"),
        (r"^/api/v1/scheduler", "scheduler"),
        (r"^/api/v1/envs", "envs"),
        (r"^/api/v1/users", "users"),
        (r"^/api/v1/logs", "logs"),
        (r"^/api/v1/dashboard", "dashboard"),
    ]

    def _match_namespace(self, path):
        for pattern, ns in self.PATH_NAMESPACE:
            if re.match(pattern, path):
                return ns
        return None

    async def dispatch(self, request, call_next):
        is_write = request.method in self.WRITE_METHODS

        response = await call_next(request)

        try:
            if is_write and (response.status_code < 400):
                path = request.url.path
                ns = self._match_namespace(path)
                prefixes = []
                if ns == "project":
                    prefixes.append("project:list:")
                    m = re.match(r"^/api/v1/projects/(\d+)", path)
                    if m:
                        pid = m.group(1)
                        prefixes.append(f"project:detail:{pid}:")
                elif ns == "scheduler":
                    prefixes.extend(["scheduler:list:", "scheduler:running:"])
                    m = re.match(r"^/api/v1/scheduler/tasks/(\d+)", path)
                    if m:
                        tid = m.group(1)
                        prefixes.append(f"scheduler:detail:{tid}:")
                elif ns == "envs":
                    prefixes.append("envs:list:")
                    m = re.match(r"^/api/v1/envs/venvs/(\d+)", path)
                    if m:
                        vid = m.group(1)
                        prefixes.append(f"envs:packages:{vid}:")
                    if re.match(r"^/api/v1/envs/python/interpreters", path) or re.match(r"^/api/v1/envs/python/versions", path):
                        prefixes.extend(["envs:interpreters:", "envs:versions:"])
                elif ns == "users":
                    prefixes.append("user:list:")
                    m = re.match(r"^/api/v1/users/(\d+)", path)
                    if m:
                        uid = m.group(1)
                        prefixes.append(f"user:detail:{uid}:")
                elif ns == "dashboard":
                    prefixes.append("metrics:")

                if prefixes:
                    for p in prefixes:
                        await unified_cache.clear_prefix(p)
                    logger.debug(f"写操作后缓存已清除: {prefixes}")
        except Exception as e:
            logger.error(f"缓存失效处理失败: {e}")

        return response
