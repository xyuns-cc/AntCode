"""
凭证服务 - Worker 端凭证管理

管理 Worker 从平台发放的注册凭证，支持持久化存储。
凭证用于 Gateway/Redis 连接时的身份验证。

通过抽象后端支持多种存储方式（文件、环境变量等）。

Requirements: 6.1, 6.2, 6.7
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from antcode_worker.services.credential.base import (
    CredentialStore,
    get_credential_store,
    reset_credential_store,
)
from antcode_worker.services.credential.registration_intent import RegistrationRequest


@dataclass(frozen=True)
class WorkerCredentials:
    """Worker 凭证数据模型"""

    worker_id: str = ""
    api_key: str = ""
    secret_key: str = ""
    gateway_host: str = ""
    gateway_port: int = 0
    redis_username: str = ""
    redis_password: str = ""
    registration_id: str = ""
    registered_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "worker_id": self.worker_id,
            "api_key": self.api_key,
            "secret_key": self.secret_key,
            "gateway_host": self.gateway_host,
            "gateway_port": self.gateway_port,
            "redis_username": self.redis_username,
            "redis_password": self.redis_password,
            "registration_id": self.registration_id,
            "registered_at": self.registered_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerCredentials":
        """从字典创建"""
        credentials = cls(
            worker_id=data.get("worker_id", ""),
            api_key=data.get("api_key", ""),
            secret_key=data.get("secret_key", ""),
            gateway_host=data.get("gateway_host", ""),
            gateway_port=data.get("gateway_port", 0),
            redis_username=data.get("redis_username", ""),
            redis_password=data.get("redis_password", ""),
            registration_id=data.get("registration_id", ""),
            registered_at=data.get("registered_at"),
        )
        if bool(credentials.redis_username) != bool(credentials.redis_password):
            raise ValueError("Worker Redis 用户名和密码必须成对存在")
        return credentials

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

    def ensure_durable_writable(self) -> None:
        """在请求服务端签发新凭证前验证持久化能力。"""
        self._store.ensure_durable_writable()

    def registration_session(self, install_key: str | None = None, request: RegistrationRequest | None = None):
        return self._store.registration_session(install_key, request)

    def finish_registration(self) -> None:
        self._store.finish_registration()

    def load(self) -> WorkerCredentials | None:
        """加载凭证（同步版本）"""
        return self._accept_loaded(self._store.load())

    async def load_async(self) -> WorkerCredentials | None:
        """加载凭证（异步版本）"""
        return self._accept_loaded(await self._store.load_async())

    def save(self, credentials: WorkerCredentials) -> bool:
        """保存凭证（同步版本）"""
        stored = self._with_registration_time(credentials)
        return self._accept_saved(stored, self._store.save(stored.to_dict()))

    async def save_async(self, credentials: WorkerCredentials) -> bool:
        """保存凭证（异步版本）"""
        stored = self._with_registration_time(credentials)
        success = await self._store.save_async(stored.to_dict())
        return self._accept_saved(stored, success)

    def clear(self) -> bool:
        """清除凭证（同步版本）"""
        return self._accept_cleared(self._store.clear())

    async def clear_async(self) -> bool:
        """清除凭证（异步版本）"""
        return self._accept_cleared(await self._store.clear_async())

    def _accept_loaded(self, data: dict[str, Any] | None) -> WorkerCredentials | None:
        if data is None:
            self._credentials = None
            return None
        credentials = WorkerCredentials.from_dict(data)
        if not credentials.is_valid():
            raise ValueError("Worker 凭证内容无效")
        self._credentials = credentials
        logger.info(
            "已加载凭证: worker_id={}, gateway={}:{}",
            credentials.worker_id,
            credentials.gateway_host,
            credentials.gateway_port,
        )
        return credentials

    def _accept_saved(self, credentials: WorkerCredentials, success: bool) -> bool:
        if not success:
            raise RuntimeError("Worker 凭证存储返回失败")
        self._credentials = credentials
        logger.info(
            "已保存凭证: worker_id={}, gateway={}:{}",
            credentials.worker_id,
            credentials.gateway_host,
            credentials.gateway_port,
        )
        return True

    def _accept_cleared(self, success: bool) -> bool:
        if not success:
            raise RuntimeError("Worker 凭证清理返回失败")
        self._credentials = None
        logger.info("已清除凭证")
        return True

    @staticmethod
    def _with_registration_time(credentials: WorkerCredentials) -> WorkerCredentials:
        if credentials.registered_at:
            return credentials
        return replace(credentials, registered_at=datetime.now(UTC).isoformat())


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
