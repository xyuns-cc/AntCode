"""Redis 任务队列实现

基于 Redis Sorted Set 的优先级队列，实现 TaskQueueBackend 接口。
适用于多 Master 节点部署场景，支持任务队列共享。
"""
from __future__ import annotations

import asyncio
from time import time
from typing import Dict, Any, Optional

from src.core.exceptions import RedisConnectionError
from src.services.scheduler.queue_backend import BaseQueueBackend, QueuedTask


class RedisQueueBackend(BaseQueueBackend):
    """Redis 任务队列
    
    基于 Redis Sorted Set 实现的优先级队列，支持 TaskQueueBackend 接口。
    使用 ZADD/ZPOPMIN 实现原子性入队/出队操作。
    优先级数值越小，优先级越高（先出队）。
    
    Redis 数据结构：
    - QUEUE_KEY (Sorted Set): 存储 task_id，score 为优先级
    - TASK_DATA_KEY:{task_id} (String): 存储任务数据 JSON
    
    错误处理：
    - 连接失败时自动重试
    - 操作失败时记录错误并抛出异常
    - 支持优雅降级
    """

    BACKEND_TYPE = "redis"
    QUEUE_KEY = "antcode:task_queue"
    TASK_DATA_PREFIX = "antcode:task_data:"

    # 重连配置
    MAX_RECONNECT_ATTEMPTS = 3
    RECONNECT_DELAY_SECONDS = 1.0

    def __init__(self, redis_url: str):
        """初始化 Redis 队列后端
        
        Args:
            redis_url: Redis 连接 URL，如 redis://localhost:6379/0
        """
        super().__init__()
        self._redis_url = redis_url
        self._redis: Optional[Any] = None
        self._lock = asyncio.Lock()
        self._reconnect_lock = asyncio.Lock()
        # 扩展统计信息，添加 Redis 特有的统计
        self._stats.update({
            "connection_errors": 0,
            "reconnect_attempts": 0,
            "reconnect_successes": 0,
        })

    def _get_task_data_key(self, task_id: str) -> str:
        """获取任务数据的 Redis key"""
        return f"{self.TASK_DATA_PREFIX}{task_id}"

    async def _ensure_connection(self) -> None:
        """确保 Redis 连接可用
        
        如果连接不存在或已断开，尝试建立新连接。
        
        Raises:
            RedisConnectionError: 当无法连接到 Redis 时
        """
        if self._redis is not None:
            # 检查现有连接是否有效
            try:
                await self._redis.ping()
                return
            except Exception:
                # 连接已断开，需要重连
                self._log_warning("Redis 连接已断开，尝试重连...")
                self._redis = None

        # 建立新连接
        await self._connect()

    async def _connect(self) -> None:
        """建立 Redis 连接
        
        Raises:
            RedisConnectionError: 当无法连接到 Redis 时
        """
        async with self._reconnect_lock:
            # 双重检查，避免重复连接
            if self._redis is not None:
                try:
                    await self._redis.ping()
                    return
                except Exception:
                    self._redis = None

            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(
                    self._redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=5.0,
                    socket_timeout=5.0,
                )
                # 测试连接
                await self._redis.ping()
                self._log_operation("连接成功", "N/A", url=self._redis_url)
            except ImportError:
                raise ImportError(
                    "需要安装 redis 包: pip install redis"
                )
            except Exception as e:
                self._stats["connection_errors"] += 1
                self._redis = None
                raise RedisConnectionError(f"无法连接到 Redis: {e}") from e

    async def _reconnect(self) -> bool:
        """尝试重新连接 Redis
        
        Returns:
            是否重连成功
        """
        for attempt in range(1, self.MAX_RECONNECT_ATTEMPTS + 1):
            self._stats["reconnect_attempts"] += 1
            self._log_operation("重连尝试", "N/A", attempt=f"{attempt}/{self.MAX_RECONNECT_ATTEMPTS}")

            try:
                # 关闭旧连接
                if self._redis is not None:
                    try:
                        await self._redis.close()
                    except Exception:
                        pass
                    self._redis = None

                # 建立新连接
                await self._connect()
                self._stats["reconnect_successes"] += 1
                self._log_operation("重连成功", "N/A")
                return True

            except Exception as e:
                self._log_warning(f"重连失败 (第 {attempt} 次): {e}")
                if attempt < self.MAX_RECONNECT_ATTEMPTS:
                    await asyncio.sleep(self.RECONNECT_DELAY_SECONDS * attempt)

        self._log_error(f"重连失败，已达到最大重试次数 ({self.MAX_RECONNECT_ATTEMPTS})")
        return False

    async def _execute_with_retry(self, operation_name: str, operation):
        """执行 Redis 操作，失败时尝试重连
        
        Args:
            operation_name: 操作名称（用于日志）
            operation: 异步操作函数
            
        Returns:
            操作结果
            
        Raises:
            RedisConnectionError: 当重连失败时
        """
        try:
            return await operation()
        except Exception as e:
            # 检查是否是连接错误
            error_str = str(e).lower()
            is_connection_error = any(
                keyword in error_str 
                for keyword in ['connection', 'timeout', 'refused', 'reset', 'closed']
            )

            if is_connection_error:
                self._log_warning(f"操作 '{operation_name}' 失败，尝试重连: {e}")
                self._stats["connection_errors"] += 1

                # 尝试重连
                if await self._reconnect():
                    # 重连成功，重试操作
                    try:
                        return await operation()
                    except Exception as retry_error:
                        self._log_error(f"操作 '{operation_name}' 重试失败", retry_error)
                        raise RedisConnectionError(
                            f"Redis 操作 '{operation_name}' 失败: {retry_error}"
                        ) from retry_error
                else:
                    raise RedisConnectionError(
                        f"Redis 连接失败，无法执行操作 '{operation_name}'"
                    ) from e
            else:
                # 非连接错误，直接抛出
                raise

    async def start(self) -> None:
        """启动队列"""
        await self._ensure_connection()
        self._running = True
        self._log_operation("启动", "N/A")

    async def stop(self) -> None:
        """停止队列"""
        self._running = False
        if self._redis is not None:
            await self._redis.close()
            self._redis = None
        self._log_operation("停止", "N/A")

    async def enqueue(
        self,
        task_id: str,
        project_id: str,
        priority: int,
        data: Dict[str, Any],
        project_type: str = "rule"
    ) -> bool:
        """入队任务
        
        使用 Redis 事务确保原子性：
        1. 检查任务是否已存在
        2. ZADD 添加到 Sorted Set
        3. SET 存储任务数据
        
        Args:
            task_id: 任务唯一标识
            project_id: 项目ID
            priority: 优先级（数值越小优先级越高）
            data: 任务数据
            project_type: 项目类型
            
        Returns:
            是否成功入队
            
        Raises:
            RedisConnectionError: 当 Redis 连接失败时
        """
        await self._ensure_connection()

        async def _do_enqueue():
            async with self._lock:
                # 检查是否已存在
                exists = await self._redis.zscore(self.QUEUE_KEY, task_id)
                if exists is not None:
                    self._log_warning("任务已在队列中，拒绝重复入队", task_id)
                    return False

                # 创建 QueuedTask
                queued_task = QueuedTask(
                    task_id=task_id,
                    project_id=project_id,
                    project_type=project_type,
                    priority=priority,
                    enqueue_time=time(),
                    data=data
                )

                # 使用 pipeline 确保原子性
                pipe = self._redis.pipeline()
                # ZADD: score 为 (priority * 1e10 + enqueue_time) 确保同优先级按时间排序
                score = priority * 1e10 + queued_task.enqueue_time
                pipe.zadd(self.QUEUE_KEY, {task_id: score})
                pipe.set(self._get_task_data_key(task_id), queued_task.to_json())
                await pipe.execute()

                self._update_stats("enqueued")
                self._log_operation("入队", task_id, priority=priority)
                return True

        return await self._execute_with_retry(f"enqueue({task_id})", _do_enqueue)

    async def dequeue(self, timeout: Optional[float] = None) -> Optional[QueuedTask]:
        """出队任务
        
        使用 ZPOPMIN 原子性地获取最高优先级任务。
        
        Args:
            timeout: 超时时间（秒），None 表示非阻塞
            
        Returns:
            任务数据或 None（队列为空时）
            
        Raises:
            RedisConnectionError: 当 Redis 连接失败时
        """
        await self._ensure_connection()

        async def _do_dequeue():
            async with self._lock:
                # ZPOPMIN 原子性地弹出最小 score 的元素
                result = await self._redis.zpopmin(self.QUEUE_KEY, count=1)

                if not result:
                    return None

                task_id, _ = result[0]

                # 获取任务数据
                task_data_key = self._get_task_data_key(task_id)
                task_json = await self._redis.get(task_data_key)

                if task_json is None:
                    self._log_warning("任务数据不存在，跳过", task_id)
                    return None

                # 删除任务数据
                await self._redis.delete(task_data_key)

                # 反序列化
                task = QueuedTask.from_json(task_json)
                self._update_stats("dequeued")
                self._log_operation("出队", task_id)
                return task

        return await self._execute_with_retry("dequeue", _do_dequeue)

    async def cancel(self, task_id: str) -> bool:
        """取消任务
        
        使用 ZREM 从 Sorted Set 中删除任务。
        
        Args:
            task_id: 任务唯一标识
            
        Returns:
            是否成功取消
            
        Raises:
            RedisConnectionError: 当 Redis 连接失败时
        """
        await self._ensure_connection()

        async def _do_cancel():
            async with self._lock:
                # 使用 pipeline 原子删除
                pipe = self._redis.pipeline()
                pipe.zrem(self.QUEUE_KEY, task_id)
                pipe.delete(self._get_task_data_key(task_id))
                results = await pipe.execute()

                # ZREM 返回删除的元素数量
                removed = results[0] > 0

                if removed:
                    self._update_stats("cancelled")
                    self._log_operation("取消", task_id)

                return removed

        return await self._execute_with_retry(f"cancel({task_id})", _do_cancel)

    async def update_priority(self, task_id: str, new_priority: int) -> bool:
        """更新任务优先级
        
        使用 ZADD XX 更新已存在任务的 score。
        
        Args:
            task_id: 任务唯一标识
            new_priority: 新优先级
            
        Returns:
            是否成功更新
            
        Raises:
            RedisConnectionError: 当 Redis 连接失败时
        """
        await self._ensure_connection()

        async def _do_update_priority():
            async with self._lock:
                # 检查任务是否存在
                task_data_key = self._get_task_data_key(task_id)
                task_json = await self._redis.get(task_data_key)

                if task_json is None:
                    return False

                # 获取原任务数据
                task = QueuedTask.from_json(task_json)
                old_priority = task.priority

                # 更新任务数据中的优先级
                task.priority = new_priority

                # 计算新 score，保留原入队时间
                new_score = new_priority * 1e10 + task.enqueue_time

                # 使用 pipeline 原子更新
                pipe = self._redis.pipeline()
                # ZADD XX: 只更新已存在的元素
                pipe.zadd(self.QUEUE_KEY, {task_id: new_score}, xx=True)
                pipe.set(task_data_key, task.to_json())
                await pipe.execute()

                # ZADD XX 返回 0 表示更新成功（没有新增）
                # 但我们需要检查元素是否存在
                exists = await self._redis.zscore(self.QUEUE_KEY, task_id)
                if exists is None:
                    return False

                self._update_stats("priority_updates")
                self._log_operation("优先级更新", task_id, old_priority=old_priority, new_priority=new_priority)
                return True

        return await self._execute_with_retry(f"update_priority({task_id})", _do_update_priority)

    async def get_status(self) -> Dict[str, Any]:
        """获取队列状态"""
        await self._ensure_connection()

        try:
            queue_depth = await self._redis.zcard(self.QUEUE_KEY)

            # 测试 Redis 连接延迟
            start = time()
            await self._redis.ping()
            latency_ms = (time() - start) * 1000

            return {
                "backend_type": self.BACKEND_TYPE,
                "queue_depth": queue_depth,
                "running": self._running,
                "redis_url": self._redis_url,
                "redis_connected": True,
                "redis_latency_ms": round(latency_ms, 2),
                "stats": self.get_stats()
            }
        except Exception as e:
            return {
                "backend_type": self.BACKEND_TYPE,
                "queue_depth": -1,
                "running": self._running,
                "redis_url": self._redis_url,
                "redis_connected": False,
                "redis_error": str(e),
                "stats": self.get_stats()
            }

    def contains(self, task_id: str) -> bool:
        """检查任务是否在队列中
        
        注意：这是同步方法，需要在事件循环中调用异步版本。
        由于 Protocol 定义为同步方法，这里使用同步检查。
        """
        # 由于 Protocol 定义为同步方法，这里需要特殊处理
        # 在实际使用中，建议使用 contains_async
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果在异步上下文中，创建一个 Future
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run, 
                        self._contains_async(task_id)
                    )
                    return future.result(timeout=5.0)
            else:
                return loop.run_until_complete(self._contains_async(task_id))
        except Exception:
            return False

    async def _contains_async(self, task_id: str) -> bool:
        """异步检查任务是否在队列中"""
        await self._ensure_connection()
        score = await self._redis.zscore(self.QUEUE_KEY, task_id)
        return score is not None

    def size(self) -> int:
        """获取队列大小
        
        注意：这是同步方法，需要在事件循环中调用异步版本。
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self._size_async()
                    )
                    return future.result(timeout=5.0)
            else:
                return loop.run_until_complete(self._size_async())
        except Exception:
            return -1

    async def _size_async(self) -> int:
        """异步获取队列大小"""
        await self._ensure_connection()
        return await self._redis.zcard(self.QUEUE_KEY)

    async def peek(self) -> Optional[QueuedTask]:
        """查看队首任务（不出队）"""
        await self._ensure_connection()

        try:
            # ZRANGE 获取最小 score 的元素
            result = await self._redis.zrange(self.QUEUE_KEY, 0, 0)

            if not result:
                return None

            task_id = result[0]
            task_json = await self._redis.get(self._get_task_data_key(task_id))

            if task_json is None:
                return None

            return QueuedTask.from_json(task_json)

        except Exception as e:
            self._log_error("查看队首任务失败", e)
            raise

    async def clear(self) -> int:
        """清空队列，返回清除的任务数"""
        await self._ensure_connection()

        async with self._lock:
            try:
                # 获取所有任务 ID
                task_ids = await self._redis.zrange(self.QUEUE_KEY, 0, -1)
                count = len(task_ids)

                if count > 0:
                    # 删除所有任务数据
                    pipe = self._redis.pipeline()
                    pipe.delete(self.QUEUE_KEY)
                    for task_id in task_ids:
                        pipe.delete(self._get_task_data_key(task_id))
                    await pipe.execute()

                return count

            except Exception as e:
                self._log_error("清空队列失败", e)
                raise
