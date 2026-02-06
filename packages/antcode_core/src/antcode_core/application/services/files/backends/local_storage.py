"""本地文件存储后端"""

import os
import uuid
from datetime import datetime
from pathlib import Path

import aiofiles

from antcode_core.common.config import settings
from antcode_core.common.hash_utils import create_hash_calculator
from antcode_core.application.services.files.backends.base import FileMetadata, FileStorageBackend


class LocalFileStorageBackend(FileStorageBackend):
    """本地文件存储后端"""

    CHUNK_SIZE = 8 * 1024 * 1024

    def __init__(self, storage_root=None, allowed_extensions=None, max_file_size=None):
        self.storage_root = storage_root or settings.LOCAL_STORAGE_PATH
        self.allowed_extensions = frozenset(allowed_extensions or settings.ALLOWED_FILE_TYPES)
        self.max_file_size = max_file_size or settings.MAX_FILE_SIZE
        Path(self.storage_root).mkdir(parents=True, exist_ok=True)

    def _get_file_extension(self, filename):
        if filename.endswith(".tar.gz"):
            return ".tar.gz"
        return Path(filename).suffix.lower()

    def validate_file_type(self, filename):
        return self._get_file_extension(filename) in self.allowed_extensions

    async def calculate_hash(self, file_stream):
        md5_hash = create_hash_calculator("md5")
        total_size = 0
        await file_stream.seek(0)

        while chunk := await file_stream.read(self.CHUNK_SIZE):
            total_size += len(chunk)
            if total_size > self.max_file_size:
                raise ValueError(f"文件超出限制: {self.max_file_size / 1024 / 1024:.0f}MB")
            md5_hash.update(chunk)

        await file_stream.seek(0)
        return md5_hash.hexdigest(), total_size

    def build_path(self, filename):
        now = datetime.now()
        extension = self._get_file_extension(filename)
        new_filename = f"{uuid.uuid4()}{extension}"
        relative_path = f"files/{now:%Y/%m/%d}/{new_filename}"

        full_dir = Path(self.storage_root) / f"files/{now:%Y/%m/%d}"
        full_dir.mkdir(parents=True, exist_ok=True)

        return relative_path

    def get_full_path(self, path):
        return os.path.join(self.storage_root, path)

    async def save(self, file_stream, filename, metadata=None):
        if not filename:
            raise ValueError("文件名不能为空")

        if not self.validate_file_type(filename):
            raise ValueError(f"不支持的文件类型，支持: {', '.join(self.allowed_extensions)}")

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
            raise OSError(f"保存失败: {e}") from e

        return FileMetadata(
            path=storage_path,
            size=file_size,
            hash=file_hash,
            extension=extension,
            created_at=datetime.now().isoformat(),
        )

    async def open(self, path):
        full_path = self.get_full_path(path)

        if not Path(full_path).exists():
            raise FileNotFoundError(f"文件不存在: {path}")

        try:
            async with aiofiles.open(full_path, "rb") as f:
                while chunk := await f.read(self.CHUNK_SIZE):
                    yield chunk
        except Exception as e:
            raise OSError(f"读取失败: {e}") from e

    async def delete(self, path):
        try:
            full_path = Path(self.storage_root) / path
            if full_path.exists():
                full_path.unlink()
                return True
            return False
        except Exception:
            return False

    async def exists(self, path):
        return Path(self.storage_root, path).exists()

    def is_s3_backend(self):
        """标识这不是 S3 后端"""
        return False
