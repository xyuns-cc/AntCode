"""本地文件存储后端"""

import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

import aiofiles

from antcode_core.common.config import settings
from antcode_core.infrastructure.storage.base import FileMetadata, FileStorageBackend


class LocalFileStorageBackend(FileStorageBackend):
    """本地文件存储后端"""

    CHUNK_SIZE = 8 * 1024 * 1024  # 8MB

    def __init__(
        self,
        storage_root: str | None = None,
        allowed_extensions: list[str] | None = None,
        max_file_size: int | None = None,
    ):
        self.storage_root = storage_root or settings.LOCAL_STORAGE_PATH
        self.allowed_extensions = frozenset(
            allowed_extensions or settings.ALLOWED_FILE_TYPES
        )
        self.max_file_size = max_file_size or settings.MAX_FILE_SIZE
        Path(self.storage_root).mkdir(parents=True, exist_ok=True)

    def _get_file_extension(self, filename: str) -> str:
        """获取文件扩展名"""
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
        relative_path = f"files/{now:%Y/%m/%d}/{new_filename}"

        full_dir = Path(self.storage_root) / f"files/{now:%Y/%m/%d}"
        full_dir.mkdir(parents=True, exist_ok=True)

        return relative_path

    def get_full_path(self, path: str) -> str:
        """获取完整路径"""
        return str(Path(self.storage_root) / path)

    async def save(
        self,
        file_stream: Any,
        filename: str,
        metadata: dict | None = None,
    ) -> FileMetadata:
        """保存文件"""
        if not filename:
            raise ValueError("文件名不能为空")

        if not self.validate_file_type(filename):
            raise ValueError(
                f"不支持的文件类型，支持: {', '.join(self.allowed_extensions)}"
            )

        file_hash, file_size = await self.calculate_hash(file_stream)
        extension = self._get_file_extension(filename)
        storage_path = self.build_path(filename)
        full_path = self.get_full_path(storage_path)

        try:
            async with aiofiles.open(full_path, "wb") as f:
                await file_stream.seek(0)
                while chunk := await file_stream.read(self.CHUNK_SIZE):
                    await f.write(chunk)
        except Exception as e:
            Path(full_path).unlink(missing_ok=True)
            raise IOError(f"保存失败: {e}") from e

        return FileMetadata(
            path=storage_path,
            size=file_size,
            hash=file_hash,
            extension=extension,
            created_at=datetime.now().isoformat(),
        )

    async def open(self, path: str) -> AsyncIterator[bytes]:
        """打开文件，返回异步字节流"""
        full_path = self.get_full_path(path)

        if not Path(full_path).exists():
            raise FileNotFoundError(f"文件不存在: {path}")

        try:
            async with aiofiles.open(full_path, "rb") as f:
                while chunk := await f.read(self.CHUNK_SIZE):
                    yield chunk
        except Exception as e:
            raise IOError(f"读取失败: {e}") from e

    async def delete(self, path: str) -> bool:
        """删除文件"""
        try:
            full_path = Path(self.storage_root) / path
            if full_path.exists():
                full_path.unlink()
                return True
            return False
        except Exception:
            return False

    async def exists(self, path: str) -> bool:
        """检查文件是否存在"""
        return Path(self.storage_root, path).exists()

    def is_s3_backend(self) -> bool:
        """标识这不是 S3 后端"""
        return False
