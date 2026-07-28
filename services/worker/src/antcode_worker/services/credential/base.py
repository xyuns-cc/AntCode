"""
凭证存储抽象基类

定义凭证存储的抽象接口，支持文件、环境变量等多种后端实现。

Requirements: 6.1, 6.2, 6.3
"""

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
    """
    凭证存储抽象基类

    定义凭证的加载、保存和清除操作接口。

    Requirements: 6.1, 6.2, 6.3
    """

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
        """
        加载凭证（同步版本）

        Returns:
            凭证字典，如果不存在或无效则返回 None

        Requirements: 6.4
        """
        pass

    @abstractmethod
    async def load_async(self) -> dict[str, Any] | None:
        """
        加载凭证（异步版本）

        Returns:
            凭证字典，如果不存在或无效则返回 None

        Requirements: 6.4
        """
        pass

    @abstractmethod
    def save(self, credentials: dict[str, Any]) -> bool:
        """
        保存凭证（同步版本）

        Args:
            credentials: 凭证字典

        Returns:
            是否保存成功

        Requirements: 6.5
        """
        pass

    @abstractmethod
    async def save_async(self, credentials: dict[str, Any]) -> bool:
        """
        保存凭证（异步版本）

        Args:
            credentials: 凭证字典

        Returns:
            是否保存成功

        Requirements: 6.5
        """
        pass

    @abstractmethod
    def clear(self) -> bool:
        """
        清除凭证（同步版本）

        Returns:
            是否清除成功

        Requirements: 6.6
        """
        pass

    @abstractmethod
    async def clear_async(self) -> bool:
        """
        清除凭证（异步版本）

        Returns:
            是否清除成功

        Requirements: 6.6
        """
        pass

    @abstractmethod
    def exists(self) -> bool:
        """
        检查凭证是否存在

        Returns:
            凭证是否存在
        """
        pass


# 全局凭证存储实例
_credential_store: CredentialStore | None = None
_credential_store_signature: tuple[str, str] | None = None


def get_credential_store(
    store_type: str = "env",
    data_root: Path | None = None,
) -> CredentialStore:
    """
    工厂方法：根据配置返回凭证存储实现

    Args:
        store_type: 存储类型 persistent 或 env
        data_root: persistent 模式下的 Worker 数据根目录

    Returns:
        凭证存储实例

    Raises:
        ValueError: 未知的凭证存储类型

    Requirements: 6.1, 6.2, 6.3
    """
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
    """重置全局凭证存储实例（用于测试）"""
    global _credential_store, _credential_store_signature
    _credential_store = None
    _credential_store_signature = None
