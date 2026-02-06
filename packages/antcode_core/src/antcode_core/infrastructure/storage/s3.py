"""S3/MinIO 文件存储后端

提供 S3 兼容的对象存储后端实现。
使用公共的 S3ClientManager 管理连接。
"""

import hashlib
import os
import uuid
from datetime import datetime
from typing import Any, AsyncIterator

from loguru import logger

from antcode_core.common.config import settings
from antcode_core.infrastructure.storage.base import FileMetadata, FileStorageBackend
from antcode_core.infrastructure.storage.s3_client import get_s3_client_manager


class S3FileStorageBackend(FileStorageBackend):
    """S3/MinIO 文件存储后端
    
    使用公共的 S3ClientManager 管理连接，避免重复创建。
    """

    CHUNK_SIZE = 8 * 1024 * 1024  # 8MB

    def __init__(
        self,
        bucket: str | None = None,
        allowed_extensions: list[str] | None = None,
        max_file_size: int | None = None,
    ):
        self.bucket = bucket or os.getenv("S3_BUCKET") or os.getenv("MINIO_BUCKET", "antcode")
        self.allowed_extensions = frozenset(
            allowed_extensions or settings.ALLOWED_FILE_TYPES
        )
        self.max_file_size = max_file_size or settings.MAX_FILE_SIZE
        
        # 使用公共客户端管理器
        self._client_manager = get_s3_client_manager()

    async def _get_client(self):
        """获取 S3 客户端（通过公共管理器）"""
        return await self._client_manager.get_client()

    async def close(self):
        """关闭 S3 客户端连接（委托给管理器）"""
        await self._client_manager.close()

    def _get_file_extension(self, filename: str) -> str:
        """获取文件扩展名"""
        from pathlib import Path
        
        if filename.endswith(".tar.gz"):
            return ".tar.gz"
        return Path(filename).suffix.lower()

    def validate_file_type(self, filename: str) -> bool:
        """验证文件类型"""
        return self._get_file_extension(filename) in self.allowed_extensions

    async def calculate_hash(self, file_stream: Any) -> tuple[str, int]:
        """计算文件哈希和大小"""
        md5_hash = hashlib.md5()
        total_size = 0
        await file_stream.seek(0)

        while chunk := await file_stream.read(self.CHUNK_SIZE):
            total_size += len(chunk)
            if total_size > self.max_file_size:
                raise ValueError(
                    f"文件超出限制: {self.max_file_size / 1024 / 1024:.0f}MB"
                )
            md5_hash.update(chunk)

        await file_stream.seek(0)
        return md5_hash.hexdigest(), total_size

    def build_path(self, filename: str) -> str:
        """构建存储路径"""
        now = datetime.now()
        extension = self._get_file_extension(filename)
        new_filename = f"{uuid.uuid4()}{extension}"
        return f"files/{now:%Y/%m/%d}/{new_filename}"

    def get_full_path(self, path: str) -> str:
        """获取完整路径（S3 URI）"""
        return f"s3://{self.bucket}/{path}"

    async def save(
        self,
        file_stream: Any,
        filename: str,
        metadata: dict | None = None,
    ) -> FileMetadata:
        """保存文件到 S3"""
        if not filename:
            raise ValueError("文件名不能为空")

        if not self.validate_file_type(filename):
            raise ValueError(
                f"不支持的文件类型，支持: {', '.join(self.allowed_extensions)}"
            )

        file_hash, file_size = await self.calculate_hash(file_stream)
        extension = self._get_file_extension(filename)
        storage_path = self.build_path(filename)

        try:
            client = await self._get_client()
            await file_stream.seek(0)
            
            # 读取全部内容
            content = await file_stream.read()
            
            # 上传到 S3
            await client.put_object(
                Bucket=self.bucket,
                Key=storage_path,
                Body=content,
                Metadata=metadata or {},
            )
            
            logger.debug(f"文件已上传到 S3: {storage_path}")
            
        except Exception as e:
            logger.error(f"S3 上传失败: {e}")
            raise IOError(f"保存失败: {e}") from e

        return FileMetadata(
            path=storage_path,
            size=file_size,
            hash=file_hash,
            extension=extension,
            created_at=datetime.now().isoformat(),
        )

    async def open(self, path: str) -> AsyncIterator[bytes]:
        """从 S3 读取文件"""
        try:
            client = await self._get_client()
            response = await client.get_object(Bucket=self.bucket, Key=path)
            
            async with response["Body"] as stream:
                while chunk := await stream.read(self.CHUNK_SIZE):
                    yield chunk
                    
        except Exception as e:
            if "NoSuchKey" in str(e):
                raise FileNotFoundError(f"文件不存在: {path}")
            raise IOError(f"读取失败: {e}") from e

    async def delete(self, path: str) -> bool:
        """从 S3 删除文件"""
        try:
            client = await self._get_client()
            await client.delete_object(Bucket=self.bucket, Key=path)
            logger.debug(f"文件已从 S3 删除: {path}")
            return True
        except Exception as e:
            logger.error(f"S3 删除失败: {path}, 错误: {e}")
            return False

    async def exists(self, path: str) -> bool:
        """检查文件是否存在于 S3"""
        try:
            client = await self._get_client()
            await client.head_object(Bucket=self.bucket, Key=path)
            return True
        except Exception:
            return False

    async def generate_presigned_url(
        self,
        path: str,
        expires_in: int = 3600,
        method: str = "get_object",
    ) -> str:
        """生成预签名 URL
        
        Args:
            path: 文件路径
            expires_in: 过期时间（秒）
            method: 操作方法（get_object 或 put_object）
            
        Returns:
            预签名 URL
        """
        client = await self._get_client()
        
        url = await client.generate_presigned_url(
            method,
            Params={"Bucket": self.bucket, "Key": path},
            ExpiresIn=expires_in,
        )
        
        return url

    async def get_file_bytes(self, path: str) -> bytes:
        """获取文件的完整字节内容
        
        Args:
            path: 文件路径
            
        Returns:
            文件字节内容
        """
        try:
            client = await self._get_client()
            response = await client.get_object(Bucket=self.bucket, Key=path)
            
            async with response["Body"] as stream:
                return await stream.read()
                
        except Exception as e:
            if "NoSuchKey" in str(e):
                raise FileNotFoundError(f"文件不存在: {path}")
            raise IOError(f"读取失败: {e}") from e

    async def get_file_size(self, path: str) -> int:
        """获取文件大小
        
        Args:
            path: 文件路径
            
        Returns:
            文件大小（字节）
        """
        try:
            client = await self._get_client()
            response = await client.head_object(Bucket=self.bucket, Key=path)
            return response.get("ContentLength", 0)
        except Exception as e:
            if "NoSuchKey" in str(e) or "404" in str(e):
                raise FileNotFoundError(f"文件不存在: {path}")
            raise IOError(f"获取文件大小失败: {e}") from e

    def is_s3_backend(self) -> bool:
        """标识这是 S3 后端"""
        return True
