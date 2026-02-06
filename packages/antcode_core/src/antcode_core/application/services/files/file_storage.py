"""文件存储服务"""

import os

from fastapi import HTTPException, status

from antcode_core.application.services.files.backends.base import get_file_storage_backend


class FileStorageService:
    """文件存储服务"""

    def __init__(self):
        self._backend = None

    @property
    def backend(self):
        if self._backend is None:
            self._backend = get_file_storage_backend()
        return self._backend

    async def save_file(self, file):
        """保存文件，返回 (path, hash, size, extension)"""
        filename = getattr(file, "filename", None)
        if not filename:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件名不能为空")

        if not self.backend.validate_file_type(filename):
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="不支持的文件类型")

        try:
            metadata = await self.backend.save(file, filename)
            return metadata.path, metadata.hash, metadata.size, metadata.extension
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    async def delete_file(self, storage_path):
        """删除文件"""
        return await self.backend.delete(storage_path)

    def get_file_path(self, storage_path):
        """获取完整路径"""
        if storage_path and os.path.isabs(storage_path):
            raise ValueError("禁止使用绝对路径，请使用存储相对路径")
        return self.backend.get_full_path(storage_path)

    async def file_exists(self, storage_path):
        """检查文件是否存在"""
        return await self.backend.exists(storage_path)


file_storage_service = FileStorageService()
