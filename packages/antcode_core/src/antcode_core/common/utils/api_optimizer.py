"""API 响应优化工具"""

import asyncio
import json
import time
from collections import deque
from functools import wraps

from loguru import logger
from pydantic import BaseModel

from antcode_core.common.hash_utils import calculate_content_hash
from antcode_core.infrastructure.cache import api_cache


class AsyncProcessor:
    """异步任务处理器"""

    def __init__(self, max_workers=10):
        self.semaphore = asyncio.Semaphore(max_workers)
        self.background_tasks: dict[str, asyncio.Task] = {}

    async def submit_background_task(self, task_id, coro, *args, **kwargs):
        """提交后台任务

        P2-a1 修复: `async with self.semaphore` 必须包在 wrapper 内部,
        覆盖真正的执行期。之前实现是先 `async with self.semaphore`,再在其中
        `create_task` 并立即返回, permit 在 create_task 返回后即释放,
        wrapper 实际执行时并不持有 permit —— 信号量对并发无任何限制作用。
        """

        async def task_wrapper():
            # 在 wrapper 内 acquire, permit 覆盖 coro 的整个执行期
            async with self.semaphore:
                try:
                    result = await coro(*args, **kwargs)
                    logger.debug(f"后台任务完成: {task_id}")
                    return result
                except Exception as e:
                    logger.error(f"后台任务失败 [{task_id}]: {e}")
                    raise
                finally:
                    self.background_tasks.pop(task_id, None)

        # 强引用挂在 self.background_tasks 上,避免被 GC 提前回收
        self.background_tasks[task_id] = asyncio.create_task(task_wrapper())
        return task_id

    async def batch_delete(self, ids, delete_fn):
        """批量并行删除,受 semaphore 限流。

        P2-25 修复: 之前实现在 `create_task` 前 acquire permit, 与 submit_background_task
        同款问题(permit 在返回 task 后立即释放, 并发无限制)。现在改为在 delete_one
        内部 `async with self.semaphore`, 覆盖真正的 IO 期。

        Args:
            ids: 待删除项的 id 列表
            delete_fn: 单项删除的 async 函数, 签名为 `async def(id) -> bool`

        Returns:
            (successes: list[id], failures: list[tuple[id, Exception | None]])
            当 delete_fn 返回 False 表示业务层判定失败(如不存在), 此时 Exception 为 None。
        """

        async def delete_one(item_id):
            async with self.semaphore:  # permit 覆盖真正的删除 IO
                try:
                    ok = await delete_fn(item_id)
                    return item_id, bool(ok), None
                except Exception as exc:
                    logger.error(f"批量删除失败 [{item_id}]: {exc}")
                    return item_id, False, exc

        # 收集 task 强引用,防止 asyncio.create_task 结果被 GC (官方文档提醒)
        tasks: list[asyncio.Task] = [asyncio.create_task(delete_one(i)) for i in ids]
        if not tasks:
            return [], []
        results = await asyncio.gather(*tasks, return_exceptions=False)

        successes: list = []
        failures: list = []
        for item_id, ok, exc in results:
            if ok:
                successes.append(item_id)
            else:
                failures.append((item_id, exc))
        return successes, failures

    async def shutdown(self, timeout: float = 30.0):
        """等待正在执行的后台任务收尾, 超时后 cancel。

        应在应用 shutdown 事件里 await, 避免 uvicorn 直接退出时后台任务被
        event loop 强杀, 造成半写数据 / 未 flush 日志。
        """
        tasks = list(self.background_tasks.values())
        if not tasks:
            return
        logger.info(f"AsyncProcessor.shutdown: 等待 {len(tasks)} 个后台任务收尾 (timeout={timeout}s)")
        _done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            logger.warning(f"AsyncProcessor.shutdown: {len(pending)} 个任务超时未完成, 发送 cancel")
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    def get_task_status(self, task_id):
        """获取任务状态"""
        task = self.background_tasks.get(task_id)
        if not task:
            return "not_found"
        if task.done():
            return "failed" if task.exception() else "completed"
        return "running"


class PerformanceMonitor:
    """性能监控器（使用 deque 优化内存）"""

    def __init__(self, max_records=1000, max_slow=100):
        self.metrics: dict[str, deque] = {}
        self.slow_requests: deque = deque(maxlen=max_slow)
        self.max_records = max_records

    def record_request(self, func_name, duration, threshold=1.0):
        """记录请求性能"""
        if func_name not in self.metrics:
            self.metrics[func_name] = deque(maxlen=self.max_records)

        self.metrics[func_name].append(duration)

        if duration > threshold:
            self.slow_requests.append(
                {
                    "function": func_name,
                    "duration": round(duration, 3),
                    "timestamp": time.time(),
                }
            )
            logger.warning(f"慢请求: {func_name} 耗时 {duration:.2f}s")

    def get_stats(self, func_name=None):
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
                "p95": round(p95_val, 3),
            }

        if func_name:
            metrics_deque = self.metrics.get(func_name)
            return calc_stats(metrics_deque) if metrics_deque else {}

        return {
            "functions": {k: calc_stats(v) for k, v in self.metrics.items()},
            "slow_requests": list(self.slow_requests)[-10:],
            "total_slow": len(self.slow_requests),
        }


# 全局实例
async_processor = AsyncProcessor()
performance_monitor = PerformanceMonitor()


def _normalize_cache_value(value):
    """标准化缓存值，避免不可序列化对象导致命中异常"""
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return {k: _normalize_cache_value(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_cache_value(v) for v in value]
    return value


def _generate_cache_key(func_name, args, kwargs):
    """生成 API 缓存键"""
    # 过滤不可序列化的参数
    filtered = {}
    for k, v in kwargs.items():
        # 跳过 FastAPI Request/Response 对象
        if hasattr(v, "url") or hasattr(v, "status_code"):
            continue
        # 跳过依赖注入的对象
        if hasattr(v, "__class__") and v.__class__.__name__ in ("TokenData", "Request"):
            continue
        normalized = _normalize_cache_value(v)
        try:
            json.dumps(normalized, sort_keys=True, default=str)
        except (TypeError, ValueError):
            continue
        filtered[k] = normalized

    content = f"{func_name}:{args}:{json.dumps(filtered, sort_keys=True, default=str)}"
    return calculate_content_hash(content)[:16]


def fast_response(
    cache_ttl=300,
    background_execution=False,
    namespace=None,
    key_prefix_fn=None,
):
    """
    快速响应装饰器

    Args:
        cache_ttl: 缓存 TTL（秒）
        background_execution: 是否后台执行
        namespace: 缓存命名空间
        key_prefix_fn: 自定义缓存键前缀函数
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            func_name = func.__name__

            # 构建缓存键
            raw_key = _generate_cache_key(f"{func.__module__}.{func_name}", args, kwargs)
            prefix_parts = [namespace] if namespace else []

            if key_prefix_fn:
                if sub := key_prefix_fn(args, kwargs):
                    prefix_parts.append(str(sub))

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
                return {
                    "task_id": task_id,
                    "status": "submitted",
                    "message": "任务已提交",
                }

            # 正常执行
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                performance_monitor.record_request(func_name, duration)
                await api_cache.set(cache_key, result, cache_ttl)
                return result
            except Exception:
                performance_monitor.record_request(func_name, time.time() - start_time)
                raise

        return wrapper

    return decorator


def monitor_performance(slow_threshold=1.0):
    """性能监控装饰器"""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.time()
            try:
                return await func(*args, **kwargs)
            finally:
                performance_monitor.record_request(func.__name__, time.time() - start, slow_threshold)

        return wrapper

    return decorator


def optimize_large_response(chunk_size=100):
    """大响应优化装饰器"""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            if hasattr(result, "data") and isinstance(result.data, list) and len(result.data) > chunk_size * 2:
                logger.debug(f"大响应: {func.__name__} 返回 {len(result.data)} 条")
            return result

        return wrapper

    return decorator
