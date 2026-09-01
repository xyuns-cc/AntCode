"""凭证存储抽象接口，支持文件、环境变量等多种后端实现。"""

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from antcode_worker.services.credential.registration_intent import (
        RegistrationIntent,
        RegistrationRequest,
    )


class CredentialStore(ABC):
    @abstractmethod
    def ensure_durable_writable(self) -> None:
        """验证该存储能够耐久保存新签发的凭证。"""
        pass

    @abstractmethod
    def registration_session(
        self,
        install_key: str | None = None,
        request: "RegistrationRequest | None" = None,
    ) -> "AbstractContextManager[RegistrationIntent | None]":
        """锁定并读取或创建可恢复注册意图。"""
        pass

    @abstractmethod
    def finish_registration(self) -> None:
        """在服务端 ACK 成功后耐久删除注册意图。"""
        pass

    @abstractmethod
    def load(self) -> dict[str, Any] | None:
        """加载凭证；不存在或无效返回 None。"""
        pass

    @abstractmethod
    async def load_async(self) -> dict[str, Any] | None:
        pass

    @abstractmethod
    def save(self, credentials: dict[str, Any]) -> bool:
        pass

    @abstractmethod
    async def save_async(self, credentials: dict[str, Any]) -> bool:
        pass

    @abstractmethod
    def clear(self) -> bool:
        pass

    @abstractmethod
    async def clear_async(self) -> bool:
        pass

    @abstractmethod
    def exists(self) -> bool:
        pass

    @abstractmethod
    def describe_location(self) -> str:
        """人类可读的凭证存放位置，用于"请清除这里再重新注册"这类运维指令。

        控制面库重建后本地凭据永久失效，报错必须指名要清哪一处；后端不同
        （文件 / 环境变量）该清的东西完全不同，所以由各实现自报，不在调用方
        猜路径。
        """
        pass


_credential_store: CredentialStore | None = None
_credential_store_signature: tuple[str, str] | None = None


def get_credential_store(
    store_type: str = "env",
    data_root: Path | None = None,
) -> CredentialStore:
    """按 ``store_type``（``env`` / ``persistent``）返回凭证存储；同签名复用同一实例。"""
    global _credential_store, _credential_store_signature

    signature = (store_type, str(data_root.resolve(strict=False)) if data_root else "")
    if _credential_store is not None and signature == _credential_store_signature:
        return _credential_store

    if store_type == "env":
        from antcode_worker.services.credential.env_store import EnvCredentialStore

        _credential_store = EnvCredentialStore()
    elif store_type == "persistent":
        from antcode_worker.services.credential.persistent_store import PersistentCredentialStore

        if data_root is None:
            raise ValueError("persistent credential store 必须提供 Worker data root")
        _credential_store = PersistentCredentialStore(data_root)
    else:
        raise ValueError(f"Unknown credential store type: {store_type}")

    _credential_store_signature = signature
    return _credential_store


def reset_credential_store() -> None:
    """仅供测试重置全局实例。"""
    global _credential_store, _credential_store_signature
    _credential_store = None
    _credential_store_signature = None
