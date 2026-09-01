"""API Key 认证模块

提供 API Key 的生成、验证和管理功能。
"""

import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from antcode_core.common.exceptions import AuthenticationError


def generate_api_key(prefix: str = "ak", length: int = 32) -> str:
    """生成 API Key

    Args:
        prefix: Key 前缀
        length: 随机部分长度（字节数）

    Returns:
        格式: {prefix}_{random_hex}
    """
    random_part = secrets.token_hex(length)
    return f"{prefix}_{random_part}"


def hash_api_key(api_key: str) -> str:
    """对 API Key 进行哈希（用于存储）

    Args:
        api_key: 原始 API Key

    Returns:
        SHA256 哈希值
    """
    return hashlib.sha256(api_key.encode()).hexdigest()


def verify_api_key_hash(api_key: str, stored_hash: str) -> bool:
    """验证 API Key 哈希

    Args:
        api_key: 原始 API Key
        stored_hash: 存储的哈希值

    Returns:
        是否匹配
    """
    computed_hash = hash_api_key(api_key)
    return hmac.compare_digest(computed_hash, stored_hash)


async def verify_api_key(api_key: str, worker_id: str | None = None) -> bool:
    """验证 API Key 是否存在于 Worker 记录中。

    任何模块不可用 / DB 异常都直接拒绝(返回 ``False``),不存在
    "开发模式 fallback" —— 防止环境降级时鉴权自动通过(P0-#4)。

    P1-10: 只按哈希查询
    - 优先按 ``api_key_hash`` 匹配当前 key
    - previous hash 只有在 grace 期未过期时才可用
    """
    if not api_key:
        return False

    try:
        from antcode_core.domain.models.worker import Worker
    except Exception:
        logger.exception("Worker 模型不可用,拒绝 API Key 鉴权(无 fallback)")
        return False

    key_hash = hash_api_key(api_key)

    try:
        query = Worker.filter(api_key_hash=key_hash)
        if worker_id:
            query = query.filter(public_id=worker_id)
        if await query.exists():
            return True

        previous_query = Worker.filter(
            api_key_previous_hash=key_hash,
            api_key_previous_expires_at__gt=datetime.now(UTC),
        )
        if worker_id:
            previous_query = previous_query.filter(public_id=worker_id)
        return await previous_query.exists()
    except Exception:
        logger.exception("API Key 查询失败,拒绝鉴权(无 fallback)")
        return False


def store_api_key(worker: Any, plain_key: str) -> None:
    """在 Worker 实例上只保存 API Key 哈希。"""
    if not plain_key:
        worker.api_key_hash = None
        return
    worker.api_key_hash = hash_api_key(plain_key)


async def rotate_worker_api_key(
    worker_id: str,
    grace_minutes: int = 30,
) -> dict[str, Any]:
    """轮换 Worker API Key，只持久化当前/旧 key 的哈希。"""
    if grace_minutes <= 0:
        raise ValueError("grace_minutes 必须大于 0")

    from antcode_core.domain.models.worker import Worker

    worker = await Worker.get_or_none(public_id=worker_id)
    if worker is None:
        raise ValueError("Worker 不存在")
    if not worker.api_key_hash:
        raise ValueError("Worker 当前 API Key 哈希不存在")

    plain_key = generate_api_key()
    worker.api_key_previous_hash = worker.api_key_hash
    worker.api_key_previous_expires_at = datetime.now(UTC) + timedelta(minutes=grace_minutes)
    store_api_key(worker, plain_key)
    await worker.save(
        update_fields=[
            "api_key_hash",
            "api_key_previous_hash",
            "api_key_previous_expires_at",
        ]
    )
    return {
        "api_key": plain_key,
        "previous_expires_at": worker.api_key_previous_expires_at,
    }


async def finalize_worker_api_key_rotation(worker_id: str) -> None:
    """结束 grace 期并立即撤销旧 API Key。"""
    from antcode_core.domain.models.worker import Worker

    worker = await Worker.get_or_none(public_id=worker_id)
    if worker is None:
        raise ValueError("Worker 不存在")
    worker.api_key_previous_hash = None
    worker.api_key_previous_expires_at = None
    await worker.save(update_fields=["api_key_previous_hash", "api_key_previous_expires_at"])


def store_secret_key(worker: Any, plain_secret: str) -> None:
    """加密保存 HMAC secret，并保留哈希用于完整性校验。"""
    from antcode_core.common.security.secret_box import secret_box

    if not plain_secret:
        worker.secret_key_hash = None
        worker.secret_key_encrypted = None
        return
    worker.secret_key_hash = hash_api_key(plain_secret)
    worker.secret_key_encrypted = secret_box.encrypt(plain_secret)


class APIKeyManager:
    """API Key 管理器"""

    def __init__(self):
        self._keys: dict[str, dict[str, Any]] = {}

    def register_key(
        self,
        key_id: str,
        key_hash: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """注册 API Key

        Args:
            key_id: Key 标识符
            key_hash: Key 的哈希值
            metadata: 可选的元数据（如权限、过期时间等）
        """
        self._keys[key_id] = {
            "hash": key_hash,
            "metadata": metadata or {},
            "created_at": time.time(),
        }
        logger.debug(f"已注册 API Key: {key_id}")

    def remove_key(self, key_id: str) -> bool:
        """移除 API Key

        Args:
            key_id: Key 标识符

        Returns:
            是否成功移除
        """
        if key_id in self._keys:
            del self._keys[key_id]
            logger.debug(f"已移除 API Key: {key_id}")
            return True
        return False

    def verify_key(self, key_id: str, api_key: str) -> dict[str, Any]:
        """验证 API Key

        Args:
            key_id: Key 标识符
            api_key: 原始 API Key

        Returns:
            Key 的元数据

        Raises:
            AuthenticationError: Key 无效
        """
        if key_id not in self._keys:
            raise AuthenticationError(f"未知的 API Key: {key_id}")

        key_info = self._keys[key_id]

        if not verify_api_key_hash(api_key, key_info["hash"]):
            raise AuthenticationError("API Key 验证失败")

        # 检查过期时间
        metadata = key_info.get("metadata", {})
        if (expires_at := metadata.get("expires_at")) and time.time() > expires_at:
            raise AuthenticationError("API Key 已过期")

        return metadata

    def get_key_metadata(self, key_id: str) -> dict[str, Any] | None:
        """获取 Key 元数据

        Args:
            key_id: Key 标识符

        Returns:
            元数据字典，不存在返回 None
        """
        if key_id in self._keys:
            return self._keys[key_id].get("metadata")
        return None


# 全局 API Key 管理器实例
api_key_manager = APIKeyManager()
