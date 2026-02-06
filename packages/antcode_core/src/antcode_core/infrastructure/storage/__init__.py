"""
Storage 模块

对象存储：
- s3_client: S3 客户端管理器（公共）
- base: 存储后端抽象接口
- s3: S3/MinIO 存储后端
- local: 本地文件存储后端
- presign: 预签名 URL 生成
- log_storage: 日志持久化存储（可插拔后端）
"""

from antcode_core.infrastructure.storage.s3_client import (
    S3ClientManager,
    get_s3_client_manager,
    get_s3_client,
    is_s3_configured,
)
from antcode_core.infrastructure.storage.base import (
    FileStorageBackend,
    FileMetadata,
    get_file_storage_backend,
    reset_file_storage_backend,
)
from antcode_core.infrastructure.storage.local import LocalFileStorageBackend
from antcode_core.infrastructure.storage.s3 import S3FileStorageBackend
from antcode_core.infrastructure.storage.presign import (
    generate_upload_url,
    generate_download_url,
    try_generate_download_url,
    is_s3_storage_enabled,
)

# 日志存储（延迟导入，避免循环依赖）
def get_log_storage():
    """获取日志存储后端"""
    from antcode_core.infrastructure.storage.log_storage import get_log_storage as _get
    return _get()


__all__ = [
    # S3 客户端管理器
    "S3ClientManager",
    "get_s3_client_manager",
    "get_s3_client",
    "is_s3_configured",
    # 文件存储
    "FileStorageBackend",
    "FileMetadata",
    "get_file_storage_backend",
    "reset_file_storage_backend",
    "LocalFileStorageBackend",
    "S3FileStorageBackend",
    "generate_upload_url",
    "generate_download_url",
    "try_generate_download_url",
    "is_s3_storage_enabled",
    # 日志存储
    "get_log_storage",
]
