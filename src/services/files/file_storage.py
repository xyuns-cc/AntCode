"""文件存储服务"""
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Tuple

import aiofiles
from fastapi import HTTPException, status

from src.core.config import settings
from src.utils.hash_utils import create_hash_calculator


class FileStorageService:
    """文件存储服务"""

    CHUNK_SIZE = 8 * 1024 * 1024  # 8MB

    def __init__(self):
        self.storage_root = settings.LOCAL_STORAGE_PATH
        self.allowed_extensions = frozenset(settings.ALLOWED_FILE_TYPES)
        self.max_file_size = settings.MAX_FILE_SIZE
        Path(self.storage_root).mkdir(parents=True, exist_ok=True)

    def _get_file_extension(self, filename: str) -> str:
        """获取文件扩展名"""
        return '.tar.gz' if filename.endswith('.tar.gz') else Path(filename).suffix.lower()

    def _validate_file_type(self, filename: str) -> bool:
        """验证文件类型"""
        return self._get_file_extension(filename) in self.allowed_extensions

    async def _calculate_md5_streaming(self, file) -> Tuple[str, int]:
        """流式计算 MD5 和文件大小"""
        md5_hash = create_hash_calculator("md5")
        total_size = 0
        await file.seek(0)

        while chunk := await file.read(self.CHUNK_SIZE):
            total_size += len(chunk)
            if total_size > self.max_file_size:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"文件超出限制: {self.max_file_size / 1024 / 1024:.0f}MB"
                )
            md5_hash.update(chunk)

        await file.seek(0)
        return md5_hash.hexdigest(), total_size

    def _generate_storage_path(self, extension: str) -> str:
        """生成存储路径"""
        now = datetime.now()
        filename = f"{uuid.uuid4()}{extension}"
        relative_path = f"files/{now:%Y/%m/%d}/{filename}"
        Path(self.storage_root, relative_path).parent.mkdir(parents=True, exist_ok=True)
        return relative_path

    async def save_file(self, file) -> Tuple[str, str, int, str]:
        """保存上传文件（流式处理）"""
        if not file.filename:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件名不能为空")

        if not self._validate_file_type(file.filename):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"不支持的文件类型，支持: {', '.join(self.allowed_extensions)}"
            )

        file_hash, file_size = await self._calculate_md5_streaming(file)
        extension = self._get_file_extension(file.filename)
        storage_path = self._generate_storage_path(extension)
        full_path = os.path.join(self.storage_root, storage_path)

        try:
            async with aiofiles.open(full_path, 'wb') as f:
                await file.seek(0)
                while chunk := await file.read(self.CHUNK_SIZE):
                    await f.write(chunk)
        except Exception as e:
            Path(full_path).unlink(missing_ok=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"保存失败: {e}")

        return storage_path, file_hash, file_size, extension

    def delete_file(self, storage_path: str) -> bool:
        """删除文件"""
        try:
            full_path = Path(self.storage_root) / storage_path
            if full_path.exists():
                full_path.unlink()
                return True
            return False
        except Exception:
            return False

    def get_file_path(self, storage_path: str) -> str:
        """获取完整路径"""
        return os.path.join(self.storage_root, storage_path)

    def file_exists(self, storage_path: str) -> bool:
        """检查文件是否存在"""
        return Path(self.storage_root, storage_path).exists()


file_storage_service = FileStorageService()
