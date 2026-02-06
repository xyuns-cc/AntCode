"""Redis 进度存储实现

基于 Redis Hash 实现进度存储，支持分布式环境。

Requirements: 3.2, 3.4, 3.5, 3.6, 3.7
"""

import time
from typing import Any

from loguru import logger

from antcode_core.common.serialization import from_json, to_json
from antcode_core.application.services.crawl.backends.progress_backend import ProgressStore
from antcode_core.infrastructure.redis.client import get_redis_client

# Redis 键前缀
KEY_PREFIX = "rule"
PROGRESS_SUFFIX = "progress"
CHECKPOINT_SUFFIX = "checkpoint"
WORKERS_SUFFIX = "workers"

# 默认配置
DEFAULT_WORKER_TTL = 60


class RedisProgressStore(ProgressStore):
    """Redis 进度存储实现

    使用 Redis Hash 存储进度数据，支持：
    - 进度数据的获取和设置
    - Worker 活跃状态注册和 TTL 过期检测
    - 检查点保存和加载

    适用于分布式生产环境。

    Requirements: 3.2, 3.4, 3.5, 3.6, 3.7
    """

    def __init__(
        self,
        redis_client=None,
        default_worker_ttl: int = DEFAULT_WORKER_TTL,
    ):
        """初始化 Redis 进度存储

        Args:
            redis_client: Redis 客户端，为 None 时自动获取
            default_worker_ttl: 默认 Worker TTL（秒）
        """
        self._redis = redis_client
        self._default_worker_ttl = default_worker_ttl

    async def _get_client(self):
        """获取 Redis 客户端"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    def _get_progress_key(self, project_id: str, batch_id: str) -> str:
        """获取进度 Redis 键"""
        return f"{KEY_PREFIX}:{project_id}:{PROGRESS_SUFFIX}:{batch_id}"

    def _get_checkpoint_key(self, project_id: str, batch_id: str) -> str:
        """获取检查点 Redis 键"""
        return f"{KEY_PREFIX}:{project_id}:{CHECKPOINT_SUFFIX}:{batch_id}"

    def _get_workers_key(self, project_id: str, batch_id: str) -> str:
        """获取 Worker 注册 Redis 键"""
        return f"{KEY_PREFIX}:{project_id}:{WORKERS_SUFFIX}:{batch_id}"

    def _decode_hash(self, data: dict) -> dict[str, Any]:
        """解码 Redis Hash 数据"""
        if not data:
            return {}

        decoded = {}
        for k, v in data.items():
            key_str = k.decode("utf-8") if isinstance(k, bytes) else k
            value_str = v.decode("utf-8") if isinstance(v, bytes) else v
            try:
                decoded[key_str] = from_json(value_str)
            except Exception:
                decoded[key_str] = value_str
        return decoded

    async def get_progress(
        self,
        project_id: str,
        batch_id: str,
    ) -> dict[str, Any] | None:
        """获取批次进度"""
        client = await self._get_client()
        key = self._get_progress_key(project_id, batch_id)

        data = await client.hgetall(key)
        if not data:
            return None

        return self._decode_hash(data)

    async def set_progress(
        self,
        project_id: str,
        batch_id: str,
        data: dict[str, Any],
    ) -> bool:
        """设置批次进度"""
        client = await self._get_client()
        key = self._get_progress_key(project_id, batch_id)

        # 先删除旧数据
        await client.delete(key)

        # 设置新数据
        if data:
            mapping = {k: to_json(v) for k, v in data.items()}
            await client.hset(key, mapping=mapping)

        return True

    async def update_progress(
        self,
        project_id: str,
        batch_id: str,
        updates: dict[str, Any],
    ) -> bool:
        """增量更新批次进度"""
        client = await self._get_client()
        key = self._get_progress_key(project_id, batch_id)

        if updates:
            mapping = {k: to_json(v) for k, v in updates.items()}
            await client.hset(key, mapping=mapping)

        return True

    async def increment_progress(
        self,
        project_id: str,
        batch_id: str,
        field: str,
        amount: int = 1,
    ) -> int:
        """原子增加进度字段值"""
        client = await self._get_client()
        key = self._get_progress_key(project_id, batch_id)

        # 使用 Lua 脚本实现原子操作
        lua_script = """
        local key = KEYS[1]
        local field = ARGV[1]
        local amount = tonumber(ARGV[2])

        local current = redis.call('HGET', key, field)
        if current then
            current = tonumber(current) or 0
        else
            current = 0
        end

        local new_value = current + amount
        redis.call('HSET', key, field, tostring(new_value))
        return new_value
        """

        result = await client.eval(lua_script, 1, key, field, str(amount))
        return int(result)

    async def register_worker(
        self,
        project_id: str,
        batch_id: str,
        worker_id: str,
        ttl: int = 60,
    ) -> bool:
        """注册活跃 Worker"""
        client = await self._get_client()
        key = self._get_workers_key(project_id, batch_id)

        # 存储时间戳和 TTL
        now = time.time()
        value = f"{now}:{ttl}"
        await client.hset(key, worker_id, value)

        logger.debug(f"注册 Worker: project={project_id}, batch={batch_id}, "
                     f"worker={worker_id}")

        return True

    async def get_active_workers(
        self,
        project_id: str,
        batch_id: str,
    ) -> list[str]:
        """获取活跃 Worker 列表"""
        client = await self._get_client()
        key = self._get_workers_key(project_id, batch_id)

        workers = await client.hgetall(key)
        if not workers:
            return []

        now = time.time()
        active = []
        expired = []

        for worker_id, value in workers.items():
            worker_str = worker_id.decode("utf-8") if isinstance(worker_id, bytes) else worker_id
            value_str = value.decode("utf-8") if isinstance(value, bytes) else value

            try:
                # 解析时间戳和 TTL，格式: "timestamp:ttl"
                parts = value_str.split(":")
                if len(parts) != 2:
                    # 格式错误，标记为过期
                    expired.append(worker_str)
                    continue

                timestamp = float(parts[0])
                ttl = int(parts[1])

                if now - timestamp < ttl:
                    active.append(worker_str)
                else:
                    expired.append(worker_str)
            except (ValueError, TypeError):
                expired.append(worker_str)

        # 清理过期 Worker
        if expired:
            await client.hdel(key, *expired)

        return active

    async def unregister_worker(
        self,
        project_id: str,
        batch_id: str,
        worker_id: str,
    ) -> bool:
        """注销 Worker"""
        client = await self._get_client()
        key = self._get_workers_key(project_id, batch_id)

        result = await client.hdel(key, worker_id)

        logger.debug(f"注销 Worker: project={project_id}, batch={batch_id}, "
                     f"worker={worker_id}")

        return bool(result)

    async def save_checkpoint(
        self,
        project_id: str,
        batch_id: str,
        checkpoint_data: dict[str, Any],
    ) -> bool:
        """保存检查点"""
        client = await self._get_client()
        key = self._get_checkpoint_key(project_id, batch_id)

        # 先删除旧数据
        await client.delete(key)

        # 设置新数据
        if checkpoint_data:
            mapping = {k: to_json(v) for k, v in checkpoint_data.items()}
            await client.hset(key, mapping=mapping)

        logger.info(f"保存检查点: project={project_id}, batch={batch_id}")

        return True

    async def load_checkpoint(
        self,
        project_id: str,
        batch_id: str,
    ) -> dict[str, Any] | None:
        """加载检查点"""
        client = await self._get_client()
        key = self._get_checkpoint_key(project_id, batch_id)

        data = await client.hgetall(key)
        if not data:
            return None

        logger.info(f"加载检查点: project={project_id}, batch={batch_id}")

        return self._decode_hash(data)

    async def delete_checkpoint(
        self,
        project_id: str,
        batch_id: str,
    ) -> bool:
        """删除检查点"""
        client = await self._get_client()
        key = self._get_checkpoint_key(project_id, batch_id)

        result = await client.delete(key)

        logger.info(f"删除检查点: project={project_id}, batch={batch_id}")

        return bool(result)

    async def clear(
        self,
        project_id: str,
        batch_id: str,
    ) -> bool:
        """清除批次所有进度数据"""
        client = await self._get_client()

        keys = [
            self._get_progress_key(project_id, batch_id),
            self._get_checkpoint_key(project_id, batch_id),
            self._get_workers_key(project_id, batch_id),
        ]

        result = await client.delete(*keys)

        logger.info(f"清除批次进度数据: project={project_id}, batch={batch_id}")

        return bool(result)
