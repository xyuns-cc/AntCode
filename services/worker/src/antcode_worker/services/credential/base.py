"""
凭证存储抽象基类

定义凭证存储的抽象接口，支持文件、环境变量等多种后端实现。

Requirements: 6.1, 6.2, 6.3
"""

from abc import ABC, abstractmethod
from typing import Any


class CredentialStore(ABC):
    """
    凭证存储抽象基类

    定义凭证的加载、保存和清除操作接口。

    Requirements: 6.1, 6.2, 6.3
    """

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


def get_credential_store(store_type: str = "file") -> CredentialStore:
    """
    工厂方法：根据配置返回凭证存储实现

    Args:
        store_type: 存储类型 ("file" 或 "env")

    Returns:
        凭证存储实例

    Raises:
        ValueError: 未知的凭证存储类型

    Requirements: 6.1, 6.2, 6.3
    """
    global _credential_store

    if _credential_store is not None:
        return _credential_store

    if store_type == "file":
        from antcode_worker.services.credential.file_store import FileCredentialStore
        _credential_store = FileCredentialStore()
    elif store_type == "env":
        from antcode_worker.services.credential.env_store import EnvCredentialStore
        _credential_store = EnvCredentialStore()
    else:
        raise ValueError(f"Unknown credential store type: {store_type}")

    return _credential_store


def reset_credential_store() -> None:
    """重置全局凭证存储实例（用于测试）"""
    global _credential_store
    _credential_store = None
