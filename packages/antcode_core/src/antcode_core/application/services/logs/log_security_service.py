"""
增强的日志权限验证服务
提供细粒度的权限控制和安全验证
"""

import time

from fastapi import HTTPException, status

from antcode_core.common.hash_utils import calculate_content_hash
from loguru import logger
from tortoise.exceptions import DoesNotExist

from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.application.services.base import QueryHelper


class LogPermissionError(Exception):
    """日志权限错误"""
    pass


class LogSecurityService:
    """日志安全服务"""
    
    def __init__(self):
        # 权限缓存，避免频繁数据库查询
        self._permission_cache = {}
        self._cache_ttl = 300  # 5分钟缓存
        
        # 访问频率限制
        self._access_limits = {}
        self._max_requests_per_minute = 60
        
    def _generate_cache_key(self, user_id, execution_id):
        """生成缓存键"""
        return f"perm:{user_id}:{execution_id}"
    
    def _is_cache_valid(self, cache_entry):
        """检查缓存是否有效"""
        return time.time() - cache_entry.get("timestamp", 0) < self._cache_ttl
    
    async def verify_log_access_permission(self, user, execution_id, 
                                         operation = "read"):
        """
        验证用户对日志的访问权限
        
        Args:
            user: 用户令牌数据
            execution_id: 执行ID
            operation: 操作类型 (read, write, delete)
            
        Returns:
            TaskRun: 执行记录
            
        Raises:
            LogPermissionError: 权限不足
            HTTPException: 其他错误
        """
        try:
            # 检查访问频率限制
            if not self._check_rate_limit(user.user_id):
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="访问频率过高，请稍后再试"
                )
            
            # 检查权限缓存
            cache_key = self._generate_cache_key(user.user_id, execution_id)
            if cache_key in self._permission_cache:
                cache_entry = self._permission_cache[cache_key]
                if self._is_cache_valid(cache_entry):
                    if cache_entry.get("has_permission"):
                        return cache_entry.get("execution")
                    else:
                        raise LogPermissionError("无权访问此执行记录")
            
            # 数据库验证（支持 execution_id UUID 和 public_id）
            execution = await TaskRun.get_or_none(execution_id=execution_id)
            if not execution:
                execution = await TaskRun.get_or_none(public_id=execution_id)
            if not execution:
                raise DoesNotExist("执行记录不存在")
            
            # 获取关联任务
            task = await Task.get(id=execution.task_id)
            
            # 检查基础权限
            if task.user_id != user.user_id:
                # 检查是否为管理员
                is_admin = await QueryHelper.is_admin(user.user_id)
                if not is_admin:
                    # 缓存权限结果
                    self._permission_cache[cache_key] = {
                        "has_permission": False,
                        "timestamp": time.time(),
                        "reason": "not_owner_or_admin"
                    }
                    raise LogPermissionError("无权访问此执行记录")
            
            # 检查操作特定权限
            await self._verify_operation_permission(user, execution, task, operation)
            
            # 缓存成功结果
            self._permission_cache[cache_key] = {
                "has_permission": True,
                "execution": execution,
                "timestamp": time.time()
            }
            
            logger.debug(f"用户 {user.user_id} 访问执行记录 {execution_id} 权限验证通过")
            return execution
            
        except DoesNotExist:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="执行记录不存在"
            )
        except LogPermissionError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权访问此执行记录"
            )
        except Exception as e:
            logger.error(f"权限验证异常: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="权限验证失败"
            )
    
    async def _verify_operation_permission(self, user, execution,
                                         task, operation):
        """验证操作权限"""
        
        # 读取权限：任务所有者或管理员
        if operation == "read":
            return  # 基础权限检查已经通过
        
        # 写入权限：仅任务所有者
        elif operation == "write":
            if task.user_id != user.user_id:
                raise LogPermissionError("仅任务所有者可以写入日志")
        
        # 删除权限：任务所有者或管理员
        elif operation == "delete":
            is_admin = await QueryHelper.is_admin(user.user_id)
            if task.user_id != user.user_id and not is_admin:
                raise LogPermissionError("仅任务所有者或管理员可以删除日志")
        
        else:
            raise LogPermissionError(f"未知操作类型: {operation}")
    
    def _check_rate_limit(self, user_id):
        """检查访问频率限制"""
        now = time.time()
        minute_ago = now - 60
        
        if user_id not in self._access_limits:
            self._access_limits[user_id] = []
        
        # 清理过期记录
        self._access_limits[user_id] = [
            timestamp for timestamp in self._access_limits[user_id]
            if timestamp > minute_ago
        ]
        
        # 检查限制
        if len(self._access_limits[user_id]) >= self._max_requests_per_minute:
            return False
        
        # 记录本次访问
        self._access_limits[user_id].append(now)
        return True
    
    def clear_permission_cache(self, user_id = None, 
                             execution_id = None):
        """清理权限缓存"""
        if user_id and execution_id:
            # 清理特定缓存
            cache_key = self._generate_cache_key(user_id, execution_id)
            self._permission_cache.pop(cache_key, None)
        elif user_id:
            # 清理用户相关缓存
            keys_to_remove = [
                key for key in self._permission_cache.keys()
                if key.startswith(f"perm:{user_id}:")
            ]
            for key in keys_to_remove:
                del self._permission_cache[key]
        else:
            # 清理全部缓存
            self._permission_cache.clear()
        
        logger.debug("权限缓存已清理")
    
    async def get_user_accessible_executions(self, user, 
                                           limit = 100):
        """获取用户可访问的执行记录ID列表"""
        try:
            # 获取用户的任务
            if hasattr(user, 'is_admin') and user.is_admin:
                # 管理员可以访问所有执行记录
                executions = await TaskRun.all().limit(limit)
            else:
                # 普通用户只能访问自己的任务执行记录
                user_tasks = await Task.filter(user_id=user.user_id)
                task_ids = [task.id for task in user_tasks]
                executions = await TaskRun.filter(task_id__in=task_ids).limit(limit)
            
            return [execution.execution_id for execution in executions]
            
        except Exception as e:
            logger.error(f"获取可访问执行记录失败: {e}")
            return []


class EnhancedErrorHandler:
    """增强的错误处理器"""
    
    def __init__(self):
        # 错误统计
        self._error_stats = {}
        self._error_patterns = []
        
    def log_error(self, error, context):
        """
        记录错误并生成错误ID
        
        Args:
            error: 异常对象
            context: 错误上下文
            
        Returns:
            str: 错误ID
        """
        error_id = self._generate_error_id(error, context)
        error_type = type(error).__name__
        
        # 更新错误统计
        self._error_stats[error_type] = self._error_stats.get(error_type, 0) + 1
        
        # 记录详细错误信息
        logger.error(f"错误ID: {error_id}, 类型: {error_type}, "
                    f"消息: {str(error)}, 上下文: {context}")
        
        # 检查是否为已知错误模式
        self._check_error_patterns(error, context)
        
        return error_id
    
    def _generate_error_id(self, error, context):
        """生成错误ID"""
        error_str = f"{type(error).__name__}:{str(error)}"
        context_str = str(context)
        hash_input = f"{error_str}:{context_str}:{time.time()}"
        
        return calculate_content_hash(hash_input)[:8]
    
    def _check_error_patterns(self, error, context):
        """检查错误模式"""
        error_type = type(error).__name__
        
        # 常见错误模式处理
        if error_type == "FileNotFoundError":
            logger.warning("检测到文件不存在错误，可能需要检查日志文件路径配置")
        elif error_type == "PermissionError":
            logger.warning("检测到权限错误，可能需要检查文件系统权限")
        elif error_type == "WebSocketDisconnect":
            logger.debug("WebSocket连接断开，这是正常现象")
        elif error_type == "asyncio.TimeoutError":
            logger.warning("检测到超时错误，可能需要优化性能或增加超时时间")
    
    def get_error_stats(self):
        """获取错误统计"""
        total_errors = sum(self._error_stats.values())
        
        return {
            "total_errors": total_errors,
            "error_types": dict(self._error_stats),
            "most_common": max(self._error_stats.items(), key=lambda x: x[1]) if self._error_stats else None,
            "error_rate": total_errors / max(time.time() - 3600, 1)  # 每小时错误率
        }


# 创建全局实例
log_security_service = LogSecurityService()
error_handler = EnhancedErrorHandler()
