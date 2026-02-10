"""Worker 注册管理服务

实现 Worker 节点的注册、心跳管理和离线检测。

需求: 2.4, 4.4
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field

from loguru import logger

from antcode_core.common.serialization import from_json, to_json
from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.control_plane import worker_heartbeat_key

# Redis 键名前缀
WORKER_REGISTRY_KEY = "workers:registry"  # Hash: worker_id -> worker_info
WORKER_BATCH_PREFIX = "worker:batch:"  # Set: batch_id -> worker_ids

# 默认配置
DEFAULT_HEARTBEAT_TTL = 300  # 心跳过期时间（秒）
DEFAULT_OFFLINE_THRESHOLD = 60  # 离线判定阈值（秒）
DEFAULT_CLEANUP_INTERVAL = 30  # 清理检查间隔（秒）


@dataclass
class WorkerInfo:
    """Worker 信息"""

    worker_id: str = ""
    batch_id: str = ""  # 当前处理的批次 ID
    active_tasks: int = 0  # 活跃任务数
    status: str = "online"  # online, offline
    registered_at: float = 0.0  # 注册时间戳
    last_heartbeat: float = 0.0  # 最后心跳时间戳
    total_completed: int = 0  # 累计完成任务数
    total_failed: int = 0  # 累计失败任务数
    metadata: dict = field(default_factory=dict)  # 额外元数据

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "worker_id": self.worker_id,
            "batch_id": self.batch_id,
            "active_tasks": self.active_tasks,
            "status": self.status,
            "registered_at": self.registered_at,
            "last_heartbeat": self.last_heartbeat,
            "total_completed": self.total_completed,
            "total_failed": self.total_failed,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> WorkerInfo:
        """从字典创建"""
        return cls(
            worker_id=data.get("worker_id", ""),
            batch_id=data.get("batch_id", ""),
            active_tasks=int(data.get("active_tasks", 0)),
            status=data.get("status", "online"),
            registered_at=float(data.get("registered_at", 0)),
            last_heartbeat=float(data.get("last_heartbeat", 0)),
            total_completed=int(data.get("total_completed", 0)),
            total_failed=int(data.get("total_failed", 0)),
            metadata=data.get("metadata", {}),
        )


class WorkerRegistryService:
    """Worker 注册管理服务

    管理 Worker 节点的注册、心跳和离线检测：
    - Worker 注册和注销
    - 心跳更新和状态维护
    - 离线检测和自动清理

    需求: 2.4, 4.4
    """

    def __init__(
        self,
        heartbeat_ttl: int = DEFAULT_HEARTBEAT_TTL,
        offline_threshold: int = DEFAULT_OFFLINE_THRESHOLD,
        cleanup_interval: int = DEFAULT_CLEANUP_INTERVAL,
        redis_client: object | None = None,
    ):
        """初始化服务

        Args:
            heartbeat_ttl: 心跳过期时间（秒）
            offline_threshold: 离线判定阈值（秒）
            cleanup_interval: 清理检查间隔（秒）
        """
        self._heartbeat_ttl = heartbeat_ttl
        self._offline_threshold = offline_threshold
        self._cleanup_interval = cleanup_interval
        self._redis_client = redis_client

        self._cleanup_task: asyncio.Task | None = None
        self._shutdown = False

        logger.info(f"初始化 Worker 注册服务: heartbeat_ttl={heartbeat_ttl}s, "
                    f"offline_threshold={offline_threshold}s")

    async def _get_redis(self):
        """获取 Redis 客户端（支持注入，便于测试）"""
        if self._redis_client is not None:
            return self._redis_client
        return await get_redis_client()

    # =========================================================================
    # Worker 注册
    # =========================================================================

    async def register_worker(
        self,
        worker_id: str,
        batch_id: str = "",
        metadata: dict = None,
    ) -> WorkerInfo:
        """注册 Worker

        Args:
            worker_id: Worker 唯一标识
            batch_id: 当前处理的批次 ID
            metadata: 额外元数据

        Returns:
            WorkerInfo 对象

        需求: 2.4 - Worker 上报心跳时系统更新 Worker 状态和活跃时间
        """
        try:
            redis = await self._get_redis()
            now = time.time()

            # 创建 Worker 信息
            worker_info = WorkerInfo(
                worker_id=worker_id,
                batch_id=batch_id,
                active_tasks=0,
                status="online",
                registered_at=now,
                last_heartbeat=now,
                total_completed=0,
                total_failed=0,
                metadata=metadata or {},
            )

            # 序列化并存储到 Hash
            worker_data = to_json(worker_info.to_dict())
            await redis.hset(WORKER_REGISTRY_KEY, worker_id, worker_data)

            # 设置心跳键（带过期时间）
            heartbeat_key = worker_heartbeat_key(worker_id)
            await redis.set(heartbeat_key, str(now), ex=self._heartbeat_ttl)

            # 如果有批次 ID，添加到批次的 Worker 集合
            if batch_id:
                batch_workers_key = f"{WORKER_BATCH_PREFIX}{batch_id}"
                await redis.sadd(batch_workers_key, worker_id)

            logger.info(f"Worker 注册成功: worker_id={worker_id}, batch_id={batch_id}")

            return worker_info

        except Exception as e:
            logger.error(f"Worker 注册失败: worker_id={worker_id}, 错误: {e}")
            raise

    async def unregister_worker(self, worker_id: str) -> bool:
        """注销 Worker

        Args:
            worker_id: Worker 唯一标识

        Returns:
            是否成功注销
        """
        try:
            redis = await self._get_redis()

            # 获取 Worker 信息以清理批次关联
            worker_info = await self.get_worker(worker_id)

            # 从注册表删除
            await redis.hdel(WORKER_REGISTRY_KEY, worker_id)

            # 删除心跳键
            heartbeat_key = worker_heartbeat_key(worker_id)
            await redis.delete(heartbeat_key)

            # 从批次的 Worker 集合中移除
            if worker_info and worker_info.batch_id:
                batch_workers_key = f"{WORKER_BATCH_PREFIX}{worker_info.batch_id}"
                await redis.srem(batch_workers_key, worker_id)

            logger.info(f"Worker 注销成功: worker_id={worker_id}")
            return True

        except Exception as e:
            logger.error(f"Worker 注销失败: worker_id={worker_id}, 错误: {e}")
            return False

    # =========================================================================
    # 心跳管理
    # =========================================================================

    async def heartbeat(
        self,
        worker_id: str,
        batch_id: str = "",
        active_tasks: int = 0,
        completed: int = 0,
        failed: int = 0,
    ) -> bool:
        """更新 Worker 心跳

        Args:
            worker_id: Worker 唯一标识
            batch_id: 当前处理的批次 ID
            active_tasks: 当前活跃任务数
            completed: 本次完成的任务数
            failed: 本次失败的任务数

        Returns:
            是否成功更新

        需求: 2.4 - Worker 上报心跳时系统更新 Worker 状态和活跃时间
        """
        try:
            redis = await self._get_redis()
            now = time.time()

            # 获取现有 Worker 信息
            worker_info = await self.get_worker(worker_id)

            if not worker_info:
                # Worker 未注册，自动注册
                logger.warning(f"Worker 未注册，自动注册: worker_id={worker_id}")
                worker_info = await self.register_worker(
                    worker_id=worker_id,
                    batch_id=batch_id,
                )

            # 更新 Worker 信息
            old_batch_id = worker_info.batch_id
            worker_info.batch_id = batch_id
            worker_info.active_tasks = active_tasks
            worker_info.status = "online"
            worker_info.last_heartbeat = now
            worker_info.total_completed += completed
            worker_info.total_failed += failed

            # 序列化并存储
            worker_data = to_json(worker_info.to_dict())
            await redis.hset(WORKER_REGISTRY_KEY, worker_id, worker_data)

            # 更新心跳键
            heartbeat_key = worker_heartbeat_key(worker_id)
            await redis.set(heartbeat_key, str(now), ex=self._heartbeat_ttl)

            # 处理批次变更
            if old_batch_id != batch_id:
                # 从旧批次移除
                if old_batch_id:
                    old_batch_key = f"{WORKER_BATCH_PREFIX}{old_batch_id}"
                    await redis.srem(old_batch_key, worker_id)

                # 添加到新批次
                if batch_id:
                    new_batch_key = f"{WORKER_BATCH_PREFIX}{batch_id}"
                    await redis.sadd(new_batch_key, worker_id)

            logger.debug(f"Worker 心跳更新: worker_id={worker_id}, "
                         f"batch_id={batch_id}, active_tasks={active_tasks}")

            return True

        except Exception as e:
            logger.error(f"Worker 心跳更新失败: worker_id={worker_id}, 错误: {e}")
            return False

    # =========================================================================
    # 查询接口
    # =========================================================================

    async def get_worker(self, worker_id: str) -> WorkerInfo | None:
        """获取 Worker 信息

        Args:
            worker_id: Worker 唯一标识

        Returns:
            WorkerInfo 对象，不存在时返回 None
        """
        try:
            redis = await self._get_redis()

            worker_data = await redis.hget(WORKER_REGISTRY_KEY, worker_id)

            if not worker_data:
                return None

            data = from_json(worker_data.decode('utf-8'))
            return WorkerInfo.from_dict(data)

        except Exception as e:
            logger.error(f"获取 Worker 信息失败: worker_id={worker_id}, 错误: {e}")
            return None

    async def get_all_workers(self) -> list[WorkerInfo]:
        """获取所有 Worker 信息

        Returns:
            WorkerInfo 列表
        """
        try:
            redis = await self._get_redis()

            all_data = await redis.hgetall(WORKER_REGISTRY_KEY)

            workers = []
            for _worker_id, worker_data in all_data.items():
                try:
                    data = from_json(worker_data.decode('utf-8'))
                    workers.append(WorkerInfo.from_dict(data))
                except Exception as e:
                    logger.warning(f"解析 Worker 数据失败: {e}")

            return workers

        except Exception as e:
            logger.error(f"获取所有 Worker 信息失败: {e}")
            return []

    async def get_online_workers(self) -> list[WorkerInfo]:
        """获取所有在线 Worker

        Returns:
            在线 WorkerInfo 列表
        """
        workers = await self.get_all_workers()
        return [w for w in workers if w.status == "online"]

    async def get_batch_workers(self, batch_id: str) -> list[WorkerInfo]:
        """获取批次的所有 Worker

        Args:
            batch_id: 批次 ID

        Returns:
            WorkerInfo 列表
        """
        try:
            redis = await self._get_redis()

            batch_workers_key = f"{WORKER_BATCH_PREFIX}{batch_id}"
            worker_ids = await redis.smembers(batch_workers_key)

            workers = []
            for worker_id in worker_ids:
                worker_id_str = worker_id.decode('utf-8') if isinstance(worker_id, bytes) else worker_id
                worker_info = await self.get_worker(worker_id_str)
                if worker_info:
                    workers.append(worker_info)

            return workers

        except Exception as e:
            logger.error(f"获取批次 Worker 失败: batch_id={batch_id}, 错误: {e}")
            return []

    async def get_active_worker_count(self, batch_id: str = None) -> int:
        """获取活跃 Worker 数量

        Args:
            batch_id: 批次 ID，为 None 时返回全局活跃数

        Returns:
            活跃 Worker 数量
        """
        if batch_id:
            workers = await self.get_batch_workers(batch_id)
            return len([w for w in workers if w.status == "online"])
        else:
            workers = await self.get_online_workers()
            return len(workers)

    # =========================================================================
    # 离线检测
    # =========================================================================

    async def check_offline_workers(self) -> list[str]:
        """检测离线 Worker

        检查所有 Worker 的心跳时间，将超过阈值的标记为离线。

        Returns:
            离线 Worker ID 列表

        需求: 4.4 - Worker 长时间无心跳时系统标记 Worker 为离线状态
        """
        try:
            redis = await self._get_redis()
            now = time.time()
            offline_workers = []

            workers = await self.get_all_workers()

            for worker in workers:
                if worker.status == "offline":
                    continue

                # 检查心跳键是否存在
                heartbeat_key = worker_heartbeat_key(worker.worker_id)
                heartbeat_exists = await redis.exists(heartbeat_key)

                # 检查心跳时间
                time_since_heartbeat = now - worker.last_heartbeat

                if not heartbeat_exists or time_since_heartbeat > self._offline_threshold:
                    # 标记为离线
                    worker.status = "offline"

                    worker_data = to_json(worker.to_dict())
                    await redis.hset(WORKER_REGISTRY_KEY, worker.worker_id, worker_data)

                    offline_workers.append(worker.worker_id)

                    logger.warning(f"Worker 离线: worker_id={worker.worker_id}, "
                                   f"last_heartbeat={time_since_heartbeat:.1f}s ago")

            return offline_workers

        except Exception as e:
            logger.error(f"检测离线 Worker 失败: {e}")
            return []

    async def cleanup_offline_workers(self, max_offline_time: int = 3600) -> int:
        """清理长时间离线的 Worker

        Args:
            max_offline_time: 最大离线时间（秒），超过此时间的 Worker 将被清理

        Returns:
            清理的 Worker 数量
        """
        try:
            now = time.time()
            cleaned_count = 0

            workers = await self.get_all_workers()

            for worker in workers:
                if worker.status != "offline":
                    continue

                time_since_heartbeat = now - worker.last_heartbeat

                if time_since_heartbeat > max_offline_time:
                    await self.unregister_worker(worker.worker_id)
                    cleaned_count += 1

                    logger.info(f"清理离线 Worker: worker_id={worker.worker_id}, "
                                f"offline_time={time_since_heartbeat:.1f}s")

            return cleaned_count

        except Exception as e:
            logger.error(f"清理离线 Worker 失败: {e}")
            return 0

    # =========================================================================
    # 后台任务
    # =========================================================================

    async def start_cleanup_task(self):
        """启动后台清理任务"""
        if self._cleanup_task and not self._cleanup_task.done():
            return

        self._shutdown = False
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"启动 Worker 清理任务: interval={self._cleanup_interval}s")

    async def stop_cleanup_task(self):
        """停止后台清理任务"""
        self._shutdown = True

        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task

            self._cleanup_task = None
            logger.info("停止 Worker 清理任务")

    async def _cleanup_loop(self):
        """清理循环"""
        while not self._shutdown:
            try:
                await asyncio.sleep(self._cleanup_interval)

                # 检测离线 Worker
                offline_workers = await self.check_offline_workers()

                if offline_workers:
                    logger.info(f"检测到 {len(offline_workers)} 个离线 Worker")

                # 清理长时间离线的 Worker
                cleaned = await self.cleanup_offline_workers()

                if cleaned > 0:
                    logger.info(f"清理了 {cleaned} 个长时间离线的 Worker")

            except asyncio.CancelledError:
                logger.debug("清理任务已取消")
                break
            except Exception as e:
                logger.error(f"清理循环异常: {e}")
                await asyncio.sleep(5)  # 短暂延迟后重试

    # =========================================================================
    # 统计信息
    # =========================================================================

    async def get_stats(self) -> dict:
        """获取统计信息

        Returns:
            统计信息字典
        """
        try:
            workers = await self.get_all_workers()

            online_count = len([w for w in workers if w.status == "online"])
            offline_count = len([w for w in workers if w.status == "offline"])
            total_active_tasks = sum(w.active_tasks for w in workers if w.status == "online")
            total_completed = sum(w.total_completed for w in workers)
            total_failed = sum(w.total_failed for w in workers)

            return {
                "total_workers": len(workers),
                "online_workers": online_count,
                "offline_workers": offline_count,
                "total_active_tasks": total_active_tasks,
                "total_completed": total_completed,
                "total_failed": total_failed,
            }

        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return {
                "total_workers": 0,
                "online_workers": 0,
                "offline_workers": 0,
                "total_active_tasks": 0,
                "total_completed": 0,
                "total_failed": 0,
                "error": str(e),
            }


# 全局服务实例
worker_registry_service = WorkerRegistryService()


def create_worker_registry_service(
    heartbeat_ttl: int = DEFAULT_HEARTBEAT_TTL,
    offline_threshold: int = DEFAULT_OFFLINE_THRESHOLD,
    cleanup_interval: int = DEFAULT_CLEANUP_INTERVAL,
    redis_client: object | None = None,
) -> WorkerRegistryService:
    """创建 Worker 注册服务实例

    Args:
        heartbeat_ttl: 心跳过期时间（秒）
        offline_threshold: 离线判定阈值（秒）
        cleanup_interval: 清理检查间隔（秒）

    Returns:
        WorkerRegistryService 实例
    """
    return WorkerRegistryService(
        heartbeat_ttl=heartbeat_ttl,
        offline_threshold=offline_threshold,
        cleanup_interval=cleanup_interval,
        redis_client=redis_client,
    )
