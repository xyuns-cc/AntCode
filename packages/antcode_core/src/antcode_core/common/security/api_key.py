"""API Key 认证模块

提供 API Key 的生成、验证和管理功能。
"""

import hashlib
import hmac
import secrets
import time
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
    """验证 API Key 是否存在于 Worker 记录中"""
    if not api_key:
        return False

    try:
        from antcode_core.domain.models.worker import Worker
    except Exception:
        return False

    query = Worker.filter(api_key=api_key)
    if worker_id:
        query = query.filter(public_id=worker_id)
    return await query.exists()


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
