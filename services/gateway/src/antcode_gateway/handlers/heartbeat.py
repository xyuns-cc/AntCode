"""
心跳/租约处理器

处理 Worker 的 ``ControlService.Lease`` 上报，把 Metrics 写到
``heartbeat:{worker_id}`` Hash（过渡期保留运维 dashboard 可见）。

P1c 改造：
- 旧 ``SendHeartbeat`` RPC 已删除，统一由 ``ControlService.Lease`` 携带 Metrics。
- 旧 ``HeartbeatHandler`` 保留接口、改名暴露为 ``LeaseHandler``，由 control_service
  直接调用。**这里 *只* 维护过渡期的 Hash 视图**；真正的 lease 状态机由 P3
  接管（``LeaseStore`` + 强一致 lease_id）。

**Validates: Requirements 6.3**
"""

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from antcode_core.infrastructure.redis import decode_stream_payload, worker_heartbeat_key
from loguru import logger


@dataclass
class HeartbeatData:
    """心跳数据"""

    worker_id: str
    status: str = "online"
    cpu: float = 0.0
    memory: float = 0.0
    disk: float = 0.0
    running_tasks: int = 0
    max_concurrent_tasks: int = 1
    version: str = ""
    os_type: str = ""
    os_version: str = ""
    python_version: str = ""
    machine_arch: str = ""
    capabilities: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class HeartbeatHandler:
    """心跳处理器

    处理 Worker 发送的心跳消息：
    1. 更新 Redis 中的 Worker 状态
    """

    # 心跳过期时间（秒）
    HEARTBEAT_TTL = 90

    def __init__(self, redis_client=None):
        """初始化处理器

        Args:
            redis_client: Redis 客户端，默认延迟初始化
        """
        self._redis_client = redis_client

    async def _get_redis_client(self):
        """获取 Redis 客户端"""
        if self._redis_client is None:
            try:
                from antcode_core.infrastructure.redis import get_redis_client

                self._redis_client = await get_redis_client()
            except ImportError:
                logger.warning("antcode_core.infrastructure.redis 不可用")
                return None
        return self._redis_client

    async def handle(self, heartbeat: HeartbeatData) -> bool:
        """处理心跳请求

        Args:
            heartbeat: 心跳数据

        Returns:
            是否成功
        """
        worker_id = heartbeat.worker_id

        logger.debug(
            f"收到 Worker 心跳: {worker_id}, status={heartbeat.status}, running_tasks={heartbeat.running_tasks}"
        )

        # 更新 Redis 状态
        redis_success = await self._update_redis_status(heartbeat)

        return redis_success

    async def _update_redis_status(self, heartbeat: HeartbeatData) -> bool:
        """更新 Redis 中的 Worker 状态

        Args:
            heartbeat: 心跳数据

        Returns:
            是否成功
        """
        redis = await self._get_redis_client()
        if redis is None:
            raise RuntimeError("Redis 不可用，无法持久化 Worker 心跳")

        try:
            worker_id = heartbeat.worker_id

            # 更新心跳信息（与 Direct 模式一致）
            heartbeat_key = worker_heartbeat_key(worker_id)
            timestamp = datetime.fromtimestamp(heartbeat.timestamp, UTC)
            status_data = {
                "status": heartbeat.status,
                "cpu_percent": str(heartbeat.cpu),
                "memory_percent": str(heartbeat.memory),
                "disk_percent": str(heartbeat.disk),
                "running_tasks": str(heartbeat.running_tasks),
                "max_concurrent_tasks": str(heartbeat.max_concurrent_tasks),
                "version": heartbeat.version,
                "os_type": heartbeat.os_type,
                "os_version": heartbeat.os_version,
                "python_version": heartbeat.python_version,
                "machine_arch": heartbeat.machine_arch,
                "timestamp": timestamp.isoformat(),
            }
            if heartbeat.capabilities:
                import json

                status_data["capabilities"] = json.dumps(heartbeat.capabilities, ensure_ascii=False)

            pipe = redis.pipeline(transaction=False)
            pipe.hset(heartbeat_key, mapping=status_data)
            pipe.expire(heartbeat_key, self.HEARTBEAT_TTL)
            await pipe.execute()

            logger.debug(f"Worker 状态已更新: {worker_id}")
            return True

        except Exception as e:
            logger.exception(f"更新 Redis 状态失败: {e}")
            return False

    async def get_worker_status(self, worker_id: str) -> dict[str, Any] | None:
        """获取 Worker 状态

        Args:
            worker_id: Worker ID

        Returns:
            状态信息，不存在返回 None
        """
        redis = await self._get_redis_client()
        if redis is None:
            return None

        try:
            data = await redis.hgetall(worker_heartbeat_key(worker_id))

            if not data:
                return None

            return decode_stream_payload(data)

        except Exception as e:
            logger.exception(f"获取 Worker 状态失败: {e}")
            return None

    async def is_worker_online(self, worker_id: str) -> bool:
        """检查 Worker 是否在线

        Args:
            worker_id: Worker ID

        Returns:
            是否在线
        """
        redis = await self._get_redis_client()
        if redis is None:
            return False

        try:
            heartbeat_key = worker_heartbeat_key(worker_id)
            return await redis.exists(heartbeat_key) > 0
        except Exception as e:
            logger.exception(f"检查 Worker 状态失败: {e}")
            return False


# P1c：control_service.Lease 调用方使用的别名（语义更准确）。
# 等 P3 接管真实 LeaseStore 后会被替换为新的 ``LeaseStore`` 实现。
LeaseHandler = HeartbeatHandler
LeaseData = HeartbeatData
