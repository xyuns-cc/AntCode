"""API 响应优化工具"""
import asyncio
import time
from collections import deque
from functools import wraps
from typing import Callable

from loguru import logger

from src.infrastructure.cache import api_cache
from src.utils.hash_utils import calculate_content_hash


class AsyncProcessor:
    """异步任务处理器"""

    def __init__(self, max_workers: int = 10):
        self.semaphore = asyncio.Semaphore(max_workers)
        self.background_tasks: dict[str, asyncio.Task] = {}

    async def submit_background_task(self, task_id: str, coro: Callable, *args, **kwargs) -> str:
        """提交后台任务"""
        async with self.semaphore:
            async def task_wrapper():
                try:
                    result = await coro(*args, **kwargs)
                    logger.debug(f"后台任务完成: {task_id}")
                    return result
                except Exception as e:
                    logger.error(f"后台任务失败 [{task_id}]: {e}")
                    raise
                finally:
                    self.background_tasks.pop(task_id, None)

            self.background_tasks[task_id] = asyncio.create_task(task_wrapper())
            return task_id

    def get_task_status(self, task_id: str) -> str:
        """获取任务状态"""
        task = self.background_tasks.get(task_id)
        if not task:
            return "not_found"
        if task.done():
            return "failed" if task.exception() else "success"
        return "running"


class PerformanceMonitor:
    """性能监控器（使用 deque 优化内存）"""

    def __init__(self, max_records: int = 1000, max_slow: int = 100):
        self.metrics: dict[str, deque] = {}
        self.slow_requests: deque = deque(maxlen=max_slow)
        self.max_records = max_records

    def record_request(self, func_name: str, duration: float, threshold: float = 1.0):
        """记录请求性能"""
        if func_name not in self.metrics:
            self.metrics[func_name] = deque(maxlen=self.max_records)

        self.metrics[func_name].append(duration)

        if duration > threshold:
            self.slow_requests.append({
                "function": func_name,
                "duration": round(duration, 3),
                "timestamp": time.time()
            })
            logger.warning(f"慢请求: {func_name} 耗时 {duration:.2f}s")

    def get_stats(self, func_name: str = None) -> dict:
        """获取性能统计"""
        def calc_stats(durations: deque) -> dict:
            if not durations:
                return {}
            # 直接在 deque 上计算基础统计，避免不必要的复制
            count = len(durations)
            total = sum(durations)
            min_val = min(durations)
            max_val = max(durations)
            # p95 需要排序，使用 heapq.nsmallest 更高效（O(n log k) vs O(n log n)）
            import heapq
            p95_idx = int(count * 0.95)
            p95_val = heapq.nsmallest(p95_idx + 1, durations)[-1] if count > 0 else 0
            return {
                "count": count,
                "avg": round(total / count, 3),
                "min": round(min_val, 3),
                "max": round(max_val, 3),
                "p95": round(p95_val, 3)
            }

        if func_name:
            metrics_deque = self.metrics.get(func_name)
            return calc_stats(metrics_deque) if metrics_deque else {}

        return {
            "functions": {k: calc_stats(v) for k, v in self.metrics.items()},
            "slow_requests": list(self.slow_requests)[-10:],
            "total_slow": len(self.slow_requests)
        }


# 全局实例
async_processor = AsyncProcessor()
performance_monitor = PerformanceMonitor()


def _generate_cache_key(func_name: str, args: tuple, kwargs: dict) -> str:
    """生成 API 缓存键"""
    # 过滤不可序列化的参数
    filtered = {}
    for k, v in kwargs.items():
        # 跳过 FastAPI Request/Response 对象
        if hasattr(v, 'url') or hasattr(v, 'status_code'):
            continue
        # 跳过依赖注入的对象
        if hasattr(v, '__class__') and v.__class__.__name__ in ('TokenData', 'Request'):
            continue
        try:
            hash(v) if not isinstance(v, (list, dict)) else str(v)
            filtered[k] = v
        except (TypeError, ValueError):
            continue

    content = f"{func_name}:{args}:{sorted(filtered.items())}"
    return calculate_content_hash(content)[:16]


def fast_response(
    cache_ttl: int = 300,
    background_execution: bool = False,
    namespace: str = None,
    key_prefix_fn: Callable = None,
):
    """
    快速响应装饰器
    
    Args:
        cache_ttl: 缓存 TTL（秒）
        background_execution: 是否后台执行
        namespace: 缓存命名空间
        key_prefix_fn: 自定义缓存键前缀函数
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            func_name = func.__name__

            # 构建缓存键
            raw_key = _generate_cache_key(f"{func.__module__}.{func_name}", args, kwargs)
            prefix_parts = [namespace] if namespace else []

            if key_prefix_fn:
                try:
                    if sub := key_prefix_fn(args, kwargs):
                        prefix_parts.append(str(sub))
                except Exception:
                    pass

            cache_key = ":".join(prefix_parts + [raw_key]) if prefix_parts else raw_key

            # 尝试缓存命中
            if cached := await api_cache.get(cache_key):
                logger.debug(f"缓存命中: {func_name}")
                return cached

            # 后台执行模式
            if background_execution:
                import uuid
                task_id = str(uuid.uuid4())
                await async_processor.submit_background_task(task_id, func, *args, **kwargs)
                return {"task_id": task_id, "status": "submitted", "message": "任务已提交"}

            # 正常执行
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                performance_monitor.record_request(func_name, duration)
                await api_cache.set(cache_key, result, cache_ttl)
                return result
            except Exception as e:
                performance_monitor.record_request(func_name, time.time() - start_time)
                raise

        return wrapper
    return decorator


def monitor_performance(slow_threshold: float = 1.0):
    """性能监控装饰器"""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.time()
            try:
                return await func(*args, **kwargs)
            finally:
                performance_monitor.record_request(func.__name__, time.time() - start, slow_threshold)
        return wrapper
    return decorator


def optimize_large_response(chunk_size: int = 100):
    """大响应优化装饰器"""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            if hasattr(result, 'data') and isinstance(result.data, list) and len(result.data) > chunk_size * 2:
                logger.debug(f"大响应: {func.__name__} 返回 {len(result.data)} 条")
            return result
        return wrapper
    return decorator


