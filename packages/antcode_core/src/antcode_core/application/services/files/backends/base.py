"""文件存储后端抽象基类"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class FileMetadata:
    """文件元数据"""

    path: str
    size: int
    hash: str
    extension: str
    content_type: str = None
    created_at: str = None

    def to_dict(self):
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
    async def save(self, file_stream, filename, metadata=None):
        """保存文件，返回 FileMetadata"""
        pass

    @abstractmethod
    async def open(self, path):
        """打开文件，返回异步字节流"""
        pass

    @abstractmethod
    async def delete(self, path):
        """删除文件"""
        pass

    @abstractmethod
    async def exists(self, path):
        """检查文件是否存在"""
        pass

    @abstractmethod
    def build_path(self, filename):
        """构建存储路径"""
        pass

    @abstractmethod
    def get_full_path(self, path):
        """获取完整路径"""
        pass

    @abstractmethod
    def validate_file_type(self, filename):
        """验证文件类型"""
        pass

    @abstractmethod
    async def calculate_hash(self, file_stream):
        """计算哈希和大小，返回 (hash, size)"""
        pass


_file_storage_backend_instance = None


def get_file_storage_backend():
    """工厂方法：根据配置返回文件存储后端"""
    global _file_storage_backend_instance

    if _file_storage_backend_instance is not None:
        return _file_storage_backend_instance

    backend_type = os.getenv("FILE_STORAGE_BACKEND", "local").lower().strip()

    if backend_type == "local":
        from antcode_core.application.services.files.backends.local_storage import LocalFileStorageBackend
        _file_storage_backend_instance = LocalFileStorageBackend()
    elif backend_type == "s3":
        from antcode_core.infrastructure.storage.s3 import S3FileStorageBackend
        _file_storage_backend_instance = S3FileStorageBackend()
    else:
        raise ValueError(f"Unknown file storage backend: {backend_type}")

    return _file_storage_backend_instance


def reset_file_storage_backend():
    """重置后端实例"""
    global _file_storage_backend_instance
    _file_storage_backend_instance = None
