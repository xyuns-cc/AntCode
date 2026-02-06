"""文件存储后端抽象层"""

from antcode_core.application.services.files.backends.base import (
    FileMetadata,
    FileStorageBackend,
    get_file_storage_backend,
    reset_file_storage_backend,
)
from antcode_core.application.services.files.backends.local_storage import LocalFileStorageBackend

__all__ = [
    "FileStorageBackend",
    "FileMetadata",
    "get_file_storage_backend",
    "reset_file_storage_backend",
    "LocalFileStorageBackend",
]
