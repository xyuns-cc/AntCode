"""
ID 生成服务

提供各种 ID 生成策略。
"""

import time
import uuid


class IdService:
    """ID 生成服务

    提供多种 ID 生成策略：
    - UUID: 标准 UUID4
    - Public ID: 不带连字符的 UUID（用于 API 暴露）
    - Execution ID: 任务执行 ID（带时间戳前缀）
    - Snowflake: 分布式唯一 ID（可选）
    """

    @staticmethod
    def generate_uuid() -> str:
        """生成标准 UUID4"""
        return str(uuid.uuid4())

    @staticmethod
    def generate_public_id() -> str:
        """生成公开 ID（不带连字符的 UUID）"""
        return uuid.uuid4().hex

    @staticmethod
    def generate_execution_id(prefix: str | None = None) -> str:
        """生成任务执行 ID

        格式: {prefix}-{timestamp}-{random}
        """
        timestamp = int(time.time() * 1000)
        random_part = uuid.uuid4().hex[:8]
        if prefix:
            return f"{prefix}-{timestamp}-{random_part}"
        return f"exec-{timestamp}-{random_part}"

    @staticmethod
    def generate_batch_id() -> str:
        """生成批次 ID"""
        timestamp = int(time.time() * 1000)
        random_part = uuid.uuid4().hex[:8]
        return f"batch-{timestamp}-{random_part}"

    @staticmethod
    def generate_stream_id() -> str:
        """生成 Redis Stream 消息 ID 前缀"""
        return f"{int(time.time() * 1000)}-0"


__all__ = [
    "IdService",
]
