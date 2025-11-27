"""API响应优化工具"""

import asyncio
import hashlib
import time
from functools import wraps

from loguru import logger

from src.core.cache import api_cache


class AsyncProcessor:
    """异步任务处理器"""
    
    def __init__(self, max_workers = 10):
        self.semaphore = asyncio.Semaphore(max_workers)
        self.background_tasks = {}
    
    async def submit_background_task(
        self,
        task_id,
        coro,
        *args,
        **kwargs
    ):
        """提交后台任务"""
        async with self.semaphore:
            async def task_wrapper():
                try:
                    result = await coro(*args, **kwargs)
                    logger.info(f"后台任务 {task_id} 完成: {result}")
                    return result
                except Exception as e:
                    logger.error(f"后台任务 {task_id} 失败: {e}")
                    raise
                finally:
                    # 清理完成的任务
                    if task_id in self.background_tasks:
                        del self.background_tasks[task_id]
            
            task = asyncio.create_task(task_wrapper())
            self.background_tasks[task_id] = task
            
            logger.info(f"后台任务 {task_id} 已提交")
            return task_id
    
    def get_task_status(self, task_id):
        """获取任务状态"""
        if task_id not in self.background_tasks:
            return "not_found"
        
        task = self.background_tasks[task_id]
        if task.done():
            if task.exception():
                return "failed"
            return "completed"
        return "running"
    
    async def get_task_result(self, task_id):
        """获取任务结果"""
        if task_id not in self.background_tasks:
            raise ValueError(f"任务 {task_id} 不存在")
        
        task = self.background_tasks[task_id]
        if not task.done():
            raise ValueError(f"任务 {task_id} 尚未完成")
        
        return await task


class PerformanceMonitor:
    """性能监控器"""
    
    def __init__(self):
        self.metrics = {}
        self.slow_requests = []
        self.max_slow_requests = 100
    
    def record_request(self, func_name, duration, threshold = 1.0):
        """记录请求性能"""
        if func_name not in self.metrics:
            self.metrics[func_name] = []
        
        self.metrics[func_name].append(duration)
        # 只保留最近1000个记录
        if len(self.metrics[func_name]) > 1000:
            self.metrics[func_name] = self.metrics[func_name][-1000:]
        
        # 记录慢请求
        if duration > threshold:
            slow_request = {
                "function": func_name,
                "duration": duration,
                "timestamp": time.time()
            }
            self.slow_requests.append(slow_request)
            
            # 限制慢请求记录数量
            if len(self.slow_requests) > self.max_slow_requests:
                self.slow_requests = self.slow_requests[-self.max_slow_requests:]
            
            logger.warning(f"慢请求警告: {func_name} 耗时 {duration:.2f}s")
    
    def get_stats(self, func_name = None):
        """获取性能统计"""
        if func_name:
            if func_name not in self.metrics:
                return {}
            
            durations = self.metrics[func_name]
            return {
                "function": func_name,
                "total_requests": len(durations),
                "avg_duration": sum(durations) / len(durations),
                "min_duration": min(durations),
                "max_duration": max(durations),
                "p95_duration": sorted(durations)[int(len(durations) * 0.95)] if durations else 0
            }
        
        # 返回所有函数的统计
        stats = {}
        for fname, durations in self.metrics.items():
            stats[fname] = {
                "total_requests": len(durations),
                "avg_duration": sum(durations) / len(durations),
                "min_duration": min(durations),
                "max_duration": max(durations),
                "p95_duration": sorted(durations)[int(len(durations) * 0.95)] if durations else 0
            }
        
        return {
            "functions": stats,
            "slow_requests": self.slow_requests[-10:],  # 最近10个慢请求
            "total_slow_requests": len(self.slow_requests)
        }


# 全局实例
async_processor = AsyncProcessor()
performance_monitor = PerformanceMonitor()


def _generate_cache_key(func_name, args, kwargs):
    """生成API缓存键"""
    # 过滤掉不可序列化的参数（如Request对象）
    filtered_kwargs = {}
    for k, v in kwargs.items():
        if hasattr(v, '__dict__') and hasattr(v, 'url'):
            # 跳过FastAPI Request对象
            continue
        try:
            str(v)  # 测试是否可序列化
            filtered_kwargs[k] = v
        except:
            continue
    
    content = f"{func_name}:{args}:{sorted(filtered_kwargs.items())}"
    return hashlib.md5(content.encode()).hexdigest()[:16]  # 使用较短的哈希


def fast_response(
    cache_ttl = 300,
    background_execution = False,
    namespace = None,
    key_prefix_fn = None,
):
    """
    快速响应装饰器 - 使用统一缓存系统
    
    Args:
        cache_ttl: 缓存时间（秒）
        background_execution: 是否后台执行
        namespace: 缓存命名空间，用于精细化失效（如 'project'/'scheduler' 等）
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            func_name = f"{func.__module__}.{func.__name__}"
            
            # 生成缓存键
            raw_key = _generate_cache_key(func_name, args, kwargs)
            prefix_parts = []
            if namespace:
                prefix_parts.append(namespace)
            if key_prefix_fn:
                try:
                    sub_prefix = key_prefix_fn(args, kwargs)
                    if sub_prefix:
                        prefix_parts.append(str(sub_prefix))
                except Exception as e:
                    logger.warning(f"key_prefix_fn 执行失败，忽略子前缀: {e}")
            cache_key = (":".join(prefix_parts) + ":" + raw_key) if prefix_parts else raw_key
            
            # 尝试从缓存获取
            cached_result = await api_cache.get(cache_key)
            if cached_result is not None:
                response_time = (time.time() - start_time) * 1000
                logger.debug(f"API缓存命中 {func.__name__}: {response_time:.2f}ms")
                return cached_result
            
            # 如果启用后台执行
            if background_execution:
                # 立即返回任务ID，后台执行
                import uuid
                task_id = str(uuid.uuid4())
                
                await async_processor.submit_background_task(
                    task_id, func, *args, **kwargs
                )
                
                return {
                    "task_id": task_id,
                    "status": "submitted",
                    "message": "任务已提交后台处理"
                }
            
            # 正常执行
            try:
                result = await func(*args, **kwargs)
                
                # 记录性能
                duration = time.time() - start_time
                performance_monitor.record_request(func.__name__, duration)
                
                # 缓存结果
                await api_cache.set(cache_key, result, cache_ttl)
                
                response_time = duration * 1000
                logger.debug(f"API响应 {func.__name__}: {response_time:.2f}ms")
                
                return result
                
            except Exception as e:
                duration = time.time() - start_time
                performance_monitor.record_request(func.__name__, duration)
                logger.error(f"API错误 {func.__name__}: {e} (耗时: {duration:.2f}s)")
                raise
        
        return wrapper
    return decorator


def monitor_performance(slow_threshold = 1.0):
    """
    性能监控装饰器
    
    Args:
        slow_threshold: 慢请求阈值（秒）
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                performance_monitor.record_request(func.__name__, duration, slow_threshold)
                return result
                
            except Exception as e:
                duration = time.time() - start_time
                performance_monitor.record_request(func.__name__, duration, slow_threshold)
                logger.error(f"函数 {func.__name__} 执行失败: {e} (耗时: {duration:.2f}s)")
                raise
        
        return wrapper
    return decorator


def optimize_large_response(chunk_size = 100):
    """
    大响应优化装饰器
    
    Args:
        chunk_size: 分页大小
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            
            # 如果是分页响应，检查数据大小
            if hasattr(result, 'data') and isinstance(result.data, list):
                if len(result.data) > chunk_size * 2:
                    logger.info(f"大响应优化: {func.__name__} 返回 {len(result.data)} 条记录")
                    # 这里可以添加分页建议或数据压缩逻辑
            
            return result
        
        return wrapper
    return decorator


# 便捷函数
async def get_performance_stats(func_name = None):
    """获取性能统计"""
    return performance_monitor.get_stats(func_name)


async def get_task_status(task_id):
    """获取后台任务状态"""
    return async_processor.get_task_status(task_id)


async def get_task_result(task_id):
    """获取后台任务结果"""
    return await async_processor.get_task_result(task_id)


async def clear_api_cache():
    """清空API缓存"""
    await api_cache.clear()
    logger.info("API缓存已清空")


async def get_cache_stats():
    """获取缓存统计"""
    return await api_cache.get_stats()
