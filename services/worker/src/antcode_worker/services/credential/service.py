"""
凭证服务 - Worker 端凭证管理

管理 Worker 从平台发放的注册凭证，支持持久化存储。
凭证用于 Gateway/Redis 连接时的身份验证。

通过抽象后端支持多种存储方式（文件、环境变量等）。

Requirements: 6.1, 6.2, 6.7
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

from antcode_worker.services.credential.base import (
    CredentialStore,
    get_credential_store,
    reset_credential_store,
)


@dataclass
class WorkerCredentials:
    """Worker 凭证数据模型"""
    worker_id: str = ""
    api_key: str = ""
    secret_key: str = ""
    gateway_host: str = ""
    gateway_port: int = 0
    registered_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "worker_id": self.worker_id,
            "api_key": self.api_key,
            "secret_key": self.secret_key,
            "gateway_host": self.gateway_host,
            "gateway_port": self.gateway_port,
            "registered_at": self.registered_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerCredentials":
        """从字典创建"""
        return cls(
            worker_id=data.get("worker_id", ""),
            api_key=data.get("api_key", ""),
            secret_key=data.get("secret_key", ""),
            gateway_host=data.get("gateway_host", ""),
            gateway_port=data.get("gateway_port", 0),
            registered_at=data.get("registered_at"),
        )

    def is_valid(self) -> bool:
        """检查凭证是否有效"""
        return bool(self.worker_id and self.api_key and self.secret_key)


class CredentialService:
    """凭证服务"""

    def __init__(self, store: CredentialStore | None = None):
        """初始化凭证服务"""
        self._store = store or get_credential_store()
        self._credentials: WorkerCredentials | None = None

    @property
    def store(self) -> CredentialStore:
        """凭证存储后端"""
        return self._store

    @property
    def credentials(self) -> WorkerCredentials | None:
        """当前凭证"""
        return self._credentials

    @property
    def has_credentials(self) -> bool:
        """是否有有效凭证"""
        return self._credentials is not None and self._credentials.is_valid()

    def load(self) -> WorkerCredentials | None:
        """加载凭证（同步版本）"""
        try:
            data = self._store.load()
            if data is None:
                return None

            self._credentials = WorkerCredentials.from_dict(data)

            if self._credentials.is_valid():
                logger.info(
                    f"已加载凭证: worker_id={self._credentials.worker_id}, "
                    f"gateway={self._credentials.gateway_host}:{self._credentials.gateway_port}"
                )
                return self._credentials
            else:
                logger.warning("凭证内容无效")
                self._credentials = None
                return None

        except Exception as e:
            logger.error(f"加载凭证失败: {e}")
            self._credentials = None
            return None

    async def load_async(self) -> WorkerCredentials | None:
        """加载凭证（异步版本）"""
        try:
            data = await self._store.load_async()
            if data is None:
                return None

            self._credentials = WorkerCredentials.from_dict(data)

            if self._credentials.is_valid():
                logger.info(
                    f"已加载凭证: worker_id={self._credentials.worker_id}, "
                    f"gateway={self._credentials.gateway_host}:{self._credentials.gateway_port}"
                )
                return self._credentials
            else:
                logger.warning("凭证内容无效")
                self._credentials = None
                return None

        except Exception as e:
            logger.error(f"加载凭证失败: {e}")
            self._credentials = None
            return None

    def save(self, credentials: WorkerCredentials) -> bool:
        """保存凭证（同步版本）"""
        try:
            if not credentials.registered_at:
                credentials.registered_at = datetime.now().isoformat()

            success = self._store.save(credentials.to_dict())

            if success:
                self._credentials = credentials
                logger.info(
                    f"已保存凭证: worker_id={credentials.worker_id}, "
                    f"gateway={credentials.gateway_host}:{credentials.gateway_port}"
                )

            return success

        except Exception as e:
            logger.error(f"保存凭证失败: {e}")
            return False

    async def save_async(self, credentials: WorkerCredentials) -> bool:
        """保存凭证（异步版本）"""
        try:
            if not credentials.registered_at:
                credentials.registered_at = datetime.now().isoformat()

            success = await self._store.save_async(credentials.to_dict())

            if success:
                self._credentials = credentials
                logger.info(
                    f"已保存凭证: worker_id={credentials.worker_id}, "
                    f"gateway={credentials.gateway_host}:{credentials.gateway_port}"
                )

            return success

        except Exception as e:
            logger.error(f"保存凭证失败: {e}")
            return False

    def clear(self) -> bool:
        """清除凭证（同步版本）"""
        try:
            success = self._store.clear()

            if success:
                self._credentials = None
                logger.info("已清除凭证")

            return success

        except Exception as e:
            logger.error(f"清除凭证失败: {e}")
            return False

    async def clear_async(self) -> bool:
        """清除凭证（异步版本）"""
        try:
            success = await self._store.clear_async()

            if success:
                self._credentials = None
                logger.info("已清除凭证")

            return success

        except Exception as e:
            logger.error(f"清除凭证失败: {e}")
            return False


_credential_service: CredentialService | None = None


def get_credential_service() -> CredentialService:
    """获取全局凭证服务实例"""
    global _credential_service
    if _credential_service is None:
        _credential_service = CredentialService()
    return _credential_service


def init_credential_service(store: CredentialStore | None = None) -> CredentialService:
    """初始化全局凭证服务"""
    global _credential_service
    reset_credential_store()
    _credential_service = CredentialService(store)
    return _credential_service
