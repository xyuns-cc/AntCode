"""
文件存储服务
处理文件上传、存储、验证等功能
"""

import hashlib
import os
import uuid
from datetime import datetime
from pathlib import Path

import aiofiles
from fastapi import HTTPException, status

from src.core.config import settings


class FileStorageService:
    """文件存储服务类"""

    def __init__(self):
        self.storage_root = getattr(settings, 'LOCAL_STORAGE_PATH', './storage/projects')
        # 从配置中获取文件限制设置
        self.allowed_extensions = set(settings.ALLOWED_FILE_TYPES)
        self.max_file_size = settings.MAX_FILE_SIZE
        # 流式处理的块大小 (8MB)
        self.chunk_size = 8 * 1024 * 1024
        self._ensure_storage_directory()
    
    def _ensure_storage_directory(self):
        """确保存储目录存在"""
        Path(self.storage_root).mkdir(parents=True, exist_ok=True)
    
    def _get_file_extension(self, filename):
        """获取文件扩展名"""
        if filename.endswith('.tar.gz'):
            return '.tar.gz'
        return Path(filename).suffix.lower()
    
    def _validate_file_type(self, filename):
        """验证文件类型"""
        extension = self._get_file_extension(filename)
        return extension in self.allowed_extensions

    def _validate_file_size(self, file_size):
        """验证文件大小"""
        return file_size <= self.max_file_size
    
    async def _calculate_md5_streaming(self, file):
        """流式计算文件MD5哈希和大小"""
        md5_hash = hashlib.md5()
        total_size = 0
        
        # 重置文件指针到开始位置
        await file.seek(0)
        
        while True:
            chunk = await file.read(self.chunk_size)
            if not chunk:
                break
                
            total_size += len(chunk)
            
            # 检查文件大小限制
            if total_size > self.max_file_size:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"文件大小超出限制。最大允许: {self.max_file_size / 1024 / 1024:.1f}MB"
                )
            
            md5_hash.update(chunk)
        
        # 重置文件指针到开始位置供后续使用
        await file.seek(0)
        
        return md5_hash.hexdigest(), total_size
    
    def _generate_storage_path(self, extension):
        """生成存储路径"""
        now = datetime.now()
        year = now.strftime('%Y')
        month = now.strftime('%m')
        day = now.strftime('%d')
        
        # 生成UUID文件名
        file_uuid = str(uuid.uuid4())
        filename = f"{file_uuid}{extension}"
        
        # 构建完整路径，在日期前加files目录分离原始文件
        relative_path = f"files/{year}/{month}/{day}/{filename}"
        full_path = os.path.join(self.storage_root, relative_path)
        
        # 确保目录存在
        Path(full_path).parent.mkdir(parents=True, exist_ok=True)
        
        return relative_path
    
    async def save_file(self, file):
        """
        保存上传的文件（使用流式处理优化内存使用）
        
        Args:
            file: 上传的文件对象
            
        Returns:
            Tuple[str, str, int, str]: (存储路径, 文件哈希, 文件大小, 文件类型)
            
        Raises:
            HTTPException: 文件验证失败时抛出异常
        """
        # 验证文件名
        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="文件名不能为空"
            )
        
        # 验证文件类型
        if not self._validate_file_type(file.filename):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"不支持的文件类型。支持的类型: {', '.join(self.allowed_extensions)}"
            )
        
        # 流式计算MD5和文件大小
        try:
            file_hash, file_size = await self._calculate_md5_streaming(file)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"读取文件失败: {str(e)}"
            )
        
        # 获取文件扩展名和类型
        extension = self._get_file_extension(file.filename)
        file_type = file.content_type or 'application/octet-stream'
        
        # 生成存储路径
        storage_path = self._generate_storage_path(extension)
        full_path = os.path.join(self.storage_root, storage_path)
        
        # 使用流式保存文件
        try:
            async with aiofiles.open(full_path, 'wb') as f:
                # 重置文件指针到开始位置
                await file.seek(0)
                
                while True:
                    chunk = await file.read(self.chunk_size)
                    if not chunk:
                        break
                    await f.write(chunk)
                    
        except Exception as e:
            # 如果保存失败，清理可能创建的文件
            if os.path.exists(full_path):
                try:
                    os.remove(full_path)
                except:
                    pass
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"文件保存失败: {str(e)}"
            )
        
        return storage_path, file_hash, file_size, file_type
    
    def delete_file(self, storage_path):
        """
        删除存储的文件
        
        Args:
            storage_path: 文件存储路径
            
        Returns:
            bool: 删除是否成功
        """
        try:
            full_path = os.path.join(self.storage_root, storage_path)
            if os.path.exists(full_path):
                os.remove(full_path)
                return True
            return False
        except Exception:
            return False
    
    def get_file_path(self, storage_path):
        """
        获取文件的完整路径
        
        Args:
            storage_path: 文件存储路径
            
        Returns:
            str: 文件的完整路径
        """
        return os.path.join(self.storage_root, storage_path)
    
    def file_exists(self, storage_path):
        """
        检查文件是否存在
        
        Args:
            storage_path: 文件存储路径
            
        Returns:
            bool: 文件是否存在
        """
        full_path = os.path.join(self.storage_root, storage_path)
        return os.path.exists(full_path)


# 创建文件存储服务实例
file_storage_service = FileStorageService()
