"""
日志存储后端抽象基类

定义日志存储的统一接口，支持：
- 实时日志写入
- 日志分片写入（大文件）
- 日志查询
- 日志归档
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator


class LogType(str, Enum):
    """日志类型"""
    STDOUT = "stdout"
    STDERR = "stderr"
    SYSTEM = "system"


@dataclass
class LogEntry:
    """日志条目"""
    run_id: str
    log_type: str = "stdout"
    content: str = ""
    sequence: int = 0
    timestamp: datetime | None = None
    level: str = "INFO"
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


@dataclass
class LogChunk:
    """日志分片"""
    run_id: str
    log_type: str = "stdout"
    data: bytes = b""
    offset: int = 0
    is_final: bool = False
    checksum: str = ""
    total_size: int = -1


@dataclass
class WriteResult:
    """写入结果"""
    success: bool
    ack_offset: int = 0
    error: str | None = None
    storage_path: str | None = None


@dataclass
class LogQueryResult:
    """日志查询结果"""
    entries: list[LogEntry]
    total: int
    has_more: bool
    next_cursor: str | None = None


class LogStorageBackend(ABC):
    """日志存储后端抽象基类
    
    支持两种写入模式：
    1. 实时日志：逐条写入，适合实时推送
    2. 分片写入：大文件分片上传，适合归档
    """

    @abstractmethod
    async def write_log(self, entry: LogEntry) -> WriteResult:
        """写入单条日志
        
        Args:
            entry: 日志条目
            
        Returns:
            写入结果
        """
        pass

    @abstractmethod
    async def write_logs_batch(self, entries: list[LogEntry]) -> WriteResult:
        """批量写入日志
        
        Args:
            entries: 日志条目列表
            
        Returns:
            写入结果
        """
        pass

    @abstractmethod
    async def write_chunk(self, chunk: LogChunk) -> WriteResult:
        """写入日志分片
        
        用于大文件分片上传。
        
        Args:
            chunk: 日志分片
            
        Returns:
            写入结果，包含 ack_offset
        """
        pass

    @abstractmethod
    async def finalize_chunks(
        self,
        run_id: str,
        log_type: str,
        total_size: int,
        checksum: str,
    ) -> WriteResult:
        """完成分片上传
        
        合并所有分片，验证完整性。
        
        Args:
            run_id: 运行 ID
            log_type: 日志类型
            total_size: 总大小
            checksum: 校验和
            
        Returns:
            写入结果，包含最终存储路径
        """
        pass

    @abstractmethod
    async def query_logs(
        self,
        run_id: str,
        log_type: str | None = None,
        start_seq: int = 0,
        limit: int = 100,
        cursor: str | None = None,
    ) -> LogQueryResult:
        """查询日志
        
        Args:
            run_id: 运行 ID
            log_type: 日志类型（可选）
            start_seq: 起始序列号
            limit: 返回数量限制
            cursor: 分页游标
            
        Returns:
            查询结果
        """
        pass

    @abstractmethod
    async def get_log_stream(
        self,
        run_id: str,
        log_type: str,
    ) -> AsyncIterator[bytes]:
        """获取日志流
        
        用于下载完整日志文件。
        
        Args:
            run_id: 运行 ID
            log_type: 日志类型
            
        Yields:
            日志内容块
        """
        pass

    @abstractmethod
    async def delete_logs(self, run_id: str) -> bool:
        """删除日志
        
        Args:
            run_id: 运行 ID
            
        Returns:
            是否删除成功
        """
        pass

    @abstractmethod
    async def get_presigned_upload_url(
        self,
        run_id: str,
        filename: str,
        content_type: str = "application/gzip",
        expires_in: int = 3600,
    ) -> dict[str, Any] | None:
        """获取预签名上传 URL
        
        用于 Worker 直接上传日志文件到存储。
        
        Args:
            run_id: 运行 ID
            filename: 文件名
            content_type: 内容类型
            expires_in: 过期时间（秒）
            
        Returns:
            包含 url, headers, final_url 的字典，或 None
        """
        pass

    @abstractmethod
    async def get_presigned_download_url(
        self,
        run_id: str,
        log_type: str,
        expires_in: int = 3600,
    ) -> str | None:
        """获取预签名下载 URL
        
        Args:
            run_id: 运行 ID
            log_type: 日志类型
            expires_in: 过期时间（秒）
            
        Returns:
            预签名 URL 或 None
        """
        pass

    async def health_check(self) -> bool:
        """健康检查
        
        Returns:
            是否健康
        """
        return True


# 全局日志存储实例
_log_storage_instance: LogStorageBackend | None = None


def get_log_storage() -> LogStorageBackend:
    """工厂方法：根据配置返回日志存储后端
    
    配置项：LOG_STORAGE_BACKEND
    - s3: S3/MinIO 存储（默认）
    - clickhouse: ClickHouse 存储（预留）
    - local: 本地文件存储（开发用）
    """
    global _log_storage_instance

    if _log_storage_instance is not None:
        return _log_storage_instance

    import os
    backend_type = os.getenv("LOG_STORAGE_BACKEND", "s3").lower().strip()

    if backend_type == "s3":
        from antcode_core.infrastructure.storage.log_storage.s3 import S3LogStorage
        _log_storage_instance = S3LogStorage()
    elif backend_type == "clickhouse":
        from antcode_core.infrastructure.storage.log_storage.clickhouse import ClickHouseLogStorage
        _log_storage_instance = ClickHouseLogStorage()
    elif backend_type == "local":
        from antcode_core.infrastructure.storage.log_storage.local import LocalLogStorage
        _log_storage_instance = LocalLogStorage()
    else:
        raise ValueError(f"未知的日志存储后端: {backend_type}")

    return _log_storage_instance


def reset_log_storage() -> None:
    """重置日志存储实例"""
    global _log_storage_instance
    _log_storage_instance = None
