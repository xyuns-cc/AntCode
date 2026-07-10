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
    """验证 API Key 是否存在于 Worker 记录中。

    任何模块不可用 / DB 异常都直接拒绝(返回 ``False``),不存在
    "开发模式 fallback" —— 防止环境降级时鉴权自动通过(P0-#4)。

    P1-10: 双路查询
    - 优先按 ``api_key_hash`` 匹配(新数据 & 未来主路径)
    - 也覆盖 ``api_key_previous_hash``(轮换 grace 期旧 key)
    - fallback 明文 ``api_key`` / ``api_key_previous`` 列以兼容尚未回填 hash 的旧数据
      (迁移完成清空明文列后可移除)
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
        # 主路径:按 hash 查询(新写入的数据 & 迁移回填后的旧数据)
        from tortoise.expressions import Q

        query = Worker.filter(Q(api_key_hash=key_hash) | Q(api_key_previous_hash=key_hash))
        if worker_id:
            query = query.filter(public_id=worker_id)
        if await query.exists():
            return True

        # fallback: 老明文列(尚未回填 hash 的历史 Worker,迁移期兼容)
        legacy_query = Worker.filter(
            Q(api_key=api_key) | Q(api_key_previous=api_key)
        )
        if worker_id:
            legacy_query = legacy_query.filter(public_id=worker_id)
        return await legacy_query.exists()
    except Exception:
        logger.exception("API Key 查询失败,拒绝鉴权(无 fallback)")
        return False


def store_api_key(worker: Any, plain_key: str) -> None:
    """P1-10: 在 Worker 实例上落 api_key + api_key_hash。

    调用方在 create/rotate/finalize 路径必须走这个 helper,以保证
    hash 列跟明文列同步(明文列本 release 仍写,下个 release 清空)。

    NOTE: 只赋值到实例,由调用方决定 ``await worker.save(...)``。
    """
    if not plain_key:
        worker.api_key = None
        worker.api_key_hash = None
        return
    worker.api_key = plain_key
    worker.api_key_hash = hash_api_key(plain_key)


def store_api_key_previous(worker: Any, plain_key: str | None) -> None:
    """P1-10: 轮换时把旧 key 落到 previous 列(明文 + hash 同步)。"""
    if not plain_key:
        worker.api_key_previous = None
        worker.api_key_previous_hash = None
        return
    worker.api_key_previous = plain_key
    worker.api_key_previous_hash = hash_api_key(plain_key)


def store_secret_key(worker: Any, plain_secret: str) -> None:
    """P1-10: 在 Worker 实例上落 secret_key + secret_key_hash。"""
    if not plain_secret:
        worker.secret_key = None
        worker.secret_key_hash = None
        return
    worker.secret_key = plain_secret
    worker.secret_key_hash = hash_api_key(plain_secret)


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
