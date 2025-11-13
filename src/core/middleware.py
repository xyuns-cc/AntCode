"""
权限中间件 - 提供额外的安全验证
"""
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from src.core.auth import jwt_auth
from src.services.users.user_service import user_service
from loguru import logger
import re
from typing import Set, Optional


class AdminPermissionMiddleware(BaseHTTPMiddleware):
    """
    管理员权限中间件
    对特定的管理员API路径进行双重权限验证
    """
    
    # 需要管理员权限的API路径模式
    ADMIN_PATHS = [
        r'^/api/v1/users/?$',  # 用户列表和创建
        r'^/api/v1/users/\d+/?$',  # 用户详情、更新、删除
        r'^/api/v1/users/\d+/reset-password/?$',  # 重置密码
        r'^/api/v1/users/cache/?.*$',  # 缓存管理
    ]
    
    # 需要排除的方法（如GET用户详情可能允许普通用户访问）
    EXCLUDED_PATHS = [
        (r'^/api/v1/users/\d+/?$', 'GET'),  # 用户详情允许普通用户访问自己的信息
    ]

    async def dispatch(self, request: Request, call_next):
        # 获取请求路径和方法
        path = request.url.path
        method = request.method
        
        # 检查是否是需要管理员权限的路径
        is_admin_path = any(re.match(pattern, path) for pattern in self.ADMIN_PATHS)
        
        if is_admin_path:
            # 检查是否在排除列表中
            is_excluded = any(
                re.match(pattern, path) and method == excluded_method
                for pattern, excluded_method in self.EXCLUDED_PATHS
            )
            
            if not is_excluded:
                # 进行管理员权限验证
                await self._verify_admin_permission(request)
        
        # 继续处理请求
        response = await call_next(request)
        return response

    async def _verify_admin_permission(self, request: Request):
        """验证管理员权限"""
        try:
            # 从请求头获取Authorization token
            auth_header = request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="缺少认证信息"
                )
            
            # 提取token
            token = auth_header.split(' ')[1]
            
            # 验证token
            token_data = jwt_auth.verify_token(token)
            
            # 从数据库验证用户是否为管理员
            user = await user_service.get_user_by_id(token_data.user_id)
            if not user or not user.is_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="需要管理员权限"
                )
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"权限验证失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="权限验证失败"
            )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    安全头部中间件
    添加安全相关的HTTP头部
    """
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # 添加安全头部
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    简单的速率限制中间件
    防止暴力破解和DoS攻击
    """
    
    def __init__(self, app, calls: int = 100, period: int = 60):
        super().__init__(app)
        self.calls = calls  # 允许的请求次数
        self.period = period  # 时间窗口（秒）
        self.requests = {}  # 存储请求记录
    
    async def dispatch(self, request: Request, call_next):
        client_ip = self._get_client_ip(request)
        current_time = __import__('time').time()
        
        # 清理过期记录
        self._cleanup_expired_records(current_time)
        
        # 检查速率限制
        if self._is_rate_limited(client_ip, current_time):
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "请求频率过高，请稍后再试"}
            )
        
        # 记录请求
        self._record_request(client_ip, current_time)
        
        return await call_next(request)
    
    def _get_client_ip(self, request: Request) -> str:
        """获取客户端IP"""
        # 优先从代理头部获取真实IP
        forwarded_for = request.headers.get('X-Forwarded-For')
        if forwarded_for:
            return forwarded_for.split(',')[0].strip()
        
        real_ip = request.headers.get('X-Real-IP')
        if real_ip:
            return real_ip
        
        return request.client.host if request.client else 'unknown'
    
    def _cleanup_expired_records(self, current_time: float):
        """清理过期的请求记录"""
        expired_ips = []
        for ip, timestamps in self.requests.items():
            # 只保留时间窗口内的记录
            self.requests[ip] = [
                t for t in timestamps 
                if current_time - t < self.period
            ]
            # 如果没有记录了，标记为待删除
            if not self.requests[ip]:
                expired_ips.append(ip)
        
        # 删除没有记录的IP
        for ip in expired_ips:
            del self.requests[ip]
    
    def _is_rate_limited(self, client_ip: str, current_time: float) -> bool:
        """检查是否达到速率限制"""
        if client_ip not in self.requests:
            return False
        
        # 统计时间窗口内的请求次数
        recent_requests = [
            t for t in self.requests[client_ip]
            if current_time - t < self.period
        ]
        
        return len(recent_requests) >= self.calls
    
    def _record_request(self, client_ip: str, current_time: float):
        """记录请求"""
        if client_ip not in self.requests:
            self.requests[client_ip] = []
        
        self.requests[client_ip].append(current_time)


class CacheInvalidationMiddleware(BaseHTTPMiddleware):
    """
    写操作缓存失效中间件
    在成功的写请求（POST/PUT/PATCH/DELETE）后，清空API相关缓存，确保读取请求拿到最新数据。
    """

    WRITE_METHODS: Set[str] = {"POST", "PUT", "PATCH", "DELETE"}
    # 路径前缀到命名空间的映射
    PATH_NAMESPACE = [
        (r"^/api/v1/projects", "project"),
        (r"^/api/v1/scheduler", "scheduler"),
        (r"^/api/v1/envs", "envs"),
        (r"^/api/v1/users", "users"),
        (r"^/api/v1/logs", "logs"),
        (r"^/api/v1/dashboard", "dashboard"),
    ]

    def _match_namespace(self, path: str) -> Optional[str]:
        for pattern, ns in self.PATH_NAMESPACE:
            if re.match(pattern, path):
                return ns
        return None

    async def dispatch(self, request: Request, call_next):
        # 仅在写操作时关注
        is_write = request.method in self.WRITE_METHODS

        response = await call_next(request)

        try:
            # 只有当写操作且请求成功(状态码<400)时才清缓存
            if is_write and (response.status_code < 400):
                path = request.url.path
                ns = self._match_namespace(path)
                prefixes: list[str] = []
                if ns == "project":
                    # 清列表缓存
                    prefixes.append("project:list:")
                    # 详情：匹配 /projects/{id}
                    m = re.match(r"^/api/v1/projects/(\d+)", path)
                    if m:
                        pid = m.group(1)
                        prefixes.append(f"project:detail:{pid}:")
                elif ns == "scheduler":
                    # 清列表和运行中缓存
                    prefixes.extend(["scheduler:list:", "scheduler:running:"])
                    m = re.match(r"^/api/v1/scheduler/tasks/(\d+)", path)
                    if m:
                        tid = m.group(1)
                        prefixes.append(f"scheduler:detail:{tid}:")
                elif ns == "envs":
                    # 常规：列表
                    prefixes.append("envs:list:")
                    # venv 相关
                    m = re.match(r"^/api/v1/envs/venvs/(\d+)", path)
                    if m:
                        vid = m.group(1)
                        prefixes.append(f"envs:packages:{vid}:")
                    # 解释器/版本变更影响相关列表
                    if re.match(r"^/api/v1/envs/python/interpreters", path) or re.match(r"^/api/v1/envs/python/versions", path):
                        prefixes.extend(["envs:interpreters:", "envs:versions:"])
                elif ns == "users":
                    # 用户相关：清除用户列表缓存
                    prefixes.append("user:list:")
                    # 用户详情（如果有）
                    m = re.match(r"^/api/v1/users/(\d+)", path)
                    if m:
                        uid = m.group(1)
                        prefixes.append(f"user:detail:{uid}:")
                elif ns == "dashboard":
                    # 仪表板：清除metrics缓存
                    prefixes.append("metrics:")

                # 执行清理（使用统一缓存，只需清理一次）
                if prefixes:
                    from src.core.cache import unified_cache
                    for p in prefixes:
                        await unified_cache.clear_prefix(p)
                    logger.info(f"已在写操作后清空缓存前缀: {prefixes}")
                else:
                    logger.debug(f"未匹配到需要清理的缓存前缀: {path}")
        except Exception as e:
            # 缓存清理失败不影响主请求返回
            logger.error(f"写操作后清缓存失败: {e}")

        return response
