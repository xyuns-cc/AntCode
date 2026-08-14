"""Redis 进度存储实现

基于 Redis Hash 实现进度存储，支持分布式环境。

Requirements: 3.2, 3.4, 3.5, 3.6, 3.7
"""

from loguru import logger

from antcode_core.application.services.crawl.backends import redis_keys as crawl_keys
from antcode_core.application.services.crawl.backends.progress_backend import ProgressStore
from antcode_core.application.services.crawl.backends.redis_progress_codec import (
    decode_hash,
    mapping_args,
)
from antcode_core.application.services.crawl.backends.redis_progress_scripts import (
    FENCE_AND_CLEAR,
    INCREMENT_ACTIVE_HASH,
    LIST_ACTIVE_WORKERS,
    REGISTER_WORKER,
    REPLACE_ACTIVE_HASH,
    UPDATE_ACTIVE_HASH,
)
from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.control_plane import redis_namespace

DEFAULT_WORKER_TTL = 60
INCREMENT_SCRIPT_RESULT_SIZE = 2


class RedisProgressStore(ProgressStore):
    """Redis Hash-based Crawl progress, checkpoint, and worker registry."""

    def __init__(
        self,
        redis_client=None,
        default_worker_ttl: int = DEFAULT_WORKER_TTL,
        namespace: str | None = None,
    ):
        """初始化 Redis 进度存储

        Args:
            redis_client: Redis 客户端，为 None 时自动获取
            default_worker_ttl: 默认 Worker TTL（秒）
        """
        self._redis = redis_client
        self._default_worker_ttl = default_worker_ttl
        self._namespace = redis_namespace(namespace)

    async def _get_client(self):
        """获取 Redis 客户端"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    def _get_progress_key(self, project_id: str, batch_id: str) -> str:
        """获取进度 Redis 键"""
        return crawl_keys.crawl_progress_key(project_id, batch_id, self._namespace)

    def _get_checkpoint_key(self, project_id: str, batch_id: str) -> str:
        """获取检查点 Redis 键"""
        return crawl_keys.crawl_checkpoint_key(project_id, batch_id, self._namespace)

    def _get_workers_key(self, project_id: str, batch_id: str) -> str:
        """获取 Worker 注册 Redis 键"""
        return crawl_keys.crawl_workers_key(project_id, batch_id, self._namespace)

    def _get_cancel_fence_key(self, project_id: str, batch_id: str) -> str:
        return crawl_keys.crawl_cancel_fence_key(project_id, batch_id, self._namespace)

    async def _write_hash(
        self,
        script: str,
        *,
        project_id: str,
        batch_id: str,
        data: dict,
    ) -> bool:
        client = await self._get_client()
        result = await client.eval(
            script,
            2,
            self._get_progress_key(project_id, batch_id),
            self._get_cancel_fence_key(project_id, batch_id),
            *mapping_args(data),
        )
        return bool(result)

    async def get_progress(
        self,
        project_id: str,
        batch_id: str,
    ) -> dict[str, object] | None:
        """获取批次进度"""
        client = await self._get_client()
        key = self._get_progress_key(project_id, batch_id)

        data = await client.hgetall(key)
        if not data:
            return None

        return decode_hash(data)

    async def set_progress(
        self,
        project_id: str,
        batch_id: str,
        data: dict,
    ) -> bool:
        """设置批次进度。

        R1-P2-20 (审查报告): 老实现 `DELETE` 后 `HSET` 非原子——读端在
        中间可能看到空 hash，UI 展示进度归零。改成用 pipeline 事务
        （MULTI/EXEC）把 DEL + HSET 打包，保证原子可见性。
        """
        return await self._write_hash(
            REPLACE_ACTIVE_HASH,
            project_id=project_id,
            batch_id=batch_id,
            data=data,
        )

    async def update_progress(
        self,
        project_id: str,
        batch_id: str,
        updates: dict,
    ) -> bool:
        """增量更新批次进度"""
        if not updates:
            return True
        return await self._write_hash(
            UPDATE_ACTIVE_HASH,
            project_id=project_id,
            batch_id=batch_id,
            data=updates,
        )

    async def increment_progress(
        self,
        project_id: str,
        batch_id: str,
        field: str,
        amount: int = 1,
    ) -> int:
        """原子增加进度字段值"""
        client = await self._get_client()
        result = await client.eval(
            INCREMENT_ACTIVE_HASH,
            2,
            self._get_progress_key(project_id, batch_id),
            self._get_cancel_fence_key(project_id, batch_id),
            field,
            amount,
        )
        if not isinstance(result, (list, tuple)) or len(result) != INCREMENT_SCRIPT_RESULT_SIZE:
            raise RuntimeError("Crawl 进度递增脚本响应无效")
        if int(result[0]) != 1:
            raise RuntimeError("Crawl 批次已取消，拒绝迟到进度写入")
        return int(result[1])

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

        result = await client.eval(
            REGISTER_WORKER,
            2,
            key,
            self._get_cancel_fence_key(project_id, batch_id),
            worker_id,
            ttl * 1000,
        )

        logger.debug(f"注册 Worker: project={project_id}, batch={batch_id}, worker={worker_id}")

        return bool(result)

    async def get_active_workers(
        self,
        project_id: str,
        batch_id: str,
    ) -> list[str]:
        """获取活跃 Worker 列表"""
        client = await self._get_client()
        key = self._get_workers_key(project_id, batch_id)

        workers = await client.eval(LIST_ACTIVE_WORKERS, 1, key)
        return [item.decode("utf-8") if isinstance(item, bytes) else str(item) for item in workers]

    async def unregister_worker(
        self,
        project_id: str,
        batch_id: str,
        worker_id: str,
    ) -> bool:
        """注销 Worker"""
        client = await self._get_client()
        key = self._get_workers_key(project_id, batch_id)

        result = await client.zrem(key, worker_id)

        logger.debug(f"注销 Worker: project={project_id}, batch={batch_id}, worker={worker_id}")

        return bool(result)

    async def save_checkpoint(
        self,
        project_id: str,
        batch_id: str,
        checkpoint_data: dict[str, object],
    ) -> bool:
        """原子替换检查点，读端不会观察到中间空值。"""
        client = await self._get_client()
        key = self._get_checkpoint_key(project_id, batch_id)
        result = await client.eval(
            REPLACE_ACTIVE_HASH,
            2,
            key,
            self._get_cancel_fence_key(project_id, batch_id),
            *mapping_args(checkpoint_data),
        )

        logger.info(f"保存检查点: project={project_id}, batch={batch_id}")

        return bool(result)

    async def load_checkpoint(
        self,
        project_id: str,
        batch_id: str,
    ) -> dict[str, object] | None:
        """加载检查点"""
        client = await self._get_client()
        key = self._get_checkpoint_key(project_id, batch_id)

        data = await client.hgetall(key)
        if not data:
            return None

        logger.info(f"加载检查点: project={project_id}, batch={batch_id}")

        return decode_hash(data)

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

    async def fence_and_clear(self, project_id: str, batch_id: str) -> bool:
        """设置取消 fence，并在同一 slot 内原子删除全部临时状态。"""
        client = await self._get_client()
        result = await client.eval(
            FENCE_AND_CLEAR,
            4,
            self._get_progress_key(project_id, batch_id),
            self._get_checkpoint_key(project_id, batch_id),
            self._get_workers_key(project_id, batch_id),
            self._get_cancel_fence_key(project_id, batch_id),
        )
        logger.info(f"取消批次进度已 fence 并清除: project={project_id}, batch={batch_id}")
        return int(result) >= 0

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
