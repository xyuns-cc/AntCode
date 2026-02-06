"""文件存储后端抽象基类

定义存储后端的统一接口。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional


@dataclass
class FileMetadata:
    """文件元数据"""

    path: str
    size: int
    hash: str
    extension: str
    content_type: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size": self.size,
            "hash": self.hash,
            "extension": self.extension,
            "content_type": self.content_type,
            "created_at": self.created_at,
        }


class FileStorageBackend(ABC):
    """文件存储后端抽象基类"""

    @abstractmethod
    async def save(
        self,
        file_stream: Any,
        filename: str,
        metadata: dict | None = None,
    ) -> FileMetadata:
        """保存文件
        
        Args:
            file_stream: 文件流
            filename: 文件名
            metadata: 可选的元数据
            
        Returns:
            FileMetadata 对象
        """
        pass

    @abstractmethod
    async def open(self, path: str) -> AsyncIterator[bytes]:
        """打开文件，返回异步字节流
        
        Args:
            path: 文件路径
            
        Yields:
            文件内容块
        """
        pass

    @abstractmethod
    async def delete(self, path: str) -> bool:
        """删除文件
        
        Args:
            path: 文件路径
            
        Returns:
            是否删除成功
        """
        pass

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """检查文件是否存在
        
        Args:
            path: 文件路径
            
        Returns:
            是否存在
        """
        pass

    @abstractmethod
    def build_path(self, filename: str) -> str:
        """构建存储路径
        
        Args:
            filename: 文件名
            
        Returns:
            存储路径
        """
        pass

    @abstractmethod
    def get_full_path(self, path: str) -> str:
        """获取完整路径
        
        Args:
            path: 相对路径
            
        Returns:
            完整路径
        """
        pass

    @abstractmethod
    def validate_file_type(self, filename: str) -> bool:
        """验证文件类型
        
        Args:
            filename: 文件名
            
        Returns:
            是否为允许的文件类型
        """
        pass

    @abstractmethod
    async def calculate_hash(self, file_stream: Any) -> tuple[str, int]:
        """计算哈希和大小
        
        Args:
            file_stream: 文件流
            
        Returns:
            (hash, size) 元组
        """
        pass


# 全局存储后端实例
_file_storage_backend_instance: FileStorageBackend | None = None


def get_file_storage_backend() -> FileStorageBackend:
    """工厂方法：根据配置返回文件存储后端"""
    global _file_storage_backend_instance

    if _file_storage_backend_instance is not None:
        return _file_storage_backend_instance

    from antcode_core.common.config import settings

    backend_type = settings.FILE_STORAGE_BACKEND.lower().strip()

    if backend_type == "local":
        from antcode_core.infrastructure.storage.local import LocalFileStorageBackend

        _file_storage_backend_instance = LocalFileStorageBackend()
    elif backend_type == "s3":
        from antcode_core.infrastructure.storage.s3 import S3FileStorageBackend

        _file_storage_backend_instance = S3FileStorageBackend()
    else:
        raise ValueError(f"未知的文件存储后端: {backend_type}")

    return _file_storage_backend_instance


def reset_file_storage_backend() -> None:
    """重置后端实例"""
    global _file_storage_backend_instance
    _file_storage_backend_instance = None
