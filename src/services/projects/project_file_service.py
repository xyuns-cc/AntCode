"""
项目文件服务
处理项目文件的查看、下载、结构分析等功能
"""

import mimetypes
import os
from pathlib import Path

from fastapi import HTTPException, status
from fastapi.responses import FileResponse
from loguru import logger

from src.core.config import settings
from src.services.files.file_storage import file_storage_service


class ProjectFileService:
    """项目文件服务类"""

    def __init__(self):
        self.storage_root = settings.LOCAL_STORAGE_PATH

    async def get_project_file_structure(self, file_path):
        """
        获取项目文件结构树
        
        Args:
            file_path: 文件路径（可能是原始文件或解压目录）
            
        Returns:
            文件结构树
        """
        try:
            full_path = file_storage_service.get_file_path(file_path)
            
            if not os.path.exists(full_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"文件路径不存在: {file_path}"
                )
            
            if os.path.isfile(full_path):
                # 如果是文件，返回文件信息
                return await self._get_file_info(full_path)
            else:
                # 如果是目录，返回目录结构
                return await self._get_directory_structure(full_path)
                
        except Exception as e:
            logger.error(f"获取文件结构失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"获取文件结构失败: {str(e)}"
            )

    async def _get_directory_structure(self, dir_path):
        """获取目录结构"""
        def build_tree(path, max_depth = 5, current_depth = 0):
            """递归构建目录树"""
            if current_depth >= max_depth:
                return {
                    "name": os.path.basename(path),
                    "type": "directory",
                    "path": os.path.relpath(path, dir_path),
                    "size": 0,
                    "children_count": len(os.listdir(path)) if os.path.isdir(path) else 0,
                    "truncated": True
                }
            
            name = os.path.basename(path) or "root"
            item = {
                "name": name,
                "type": "directory" if os.path.isdir(path) else "file",
                "path": os.path.relpath(path, dir_path) if path != dir_path else "",
                "size": 0,
                "modified_time": os.path.getmtime(path)
            }
            
            if os.path.isdir(path):
                children = []
                total_size = 0
                
                try:
                    entries = os.listdir(path)
                    # 排序：目录在前，文件在后，按名称排序
                    entries.sort(key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
                    
                    for entry in entries:
                        # 跳过隐藏文件和系统文件
                        if entry.startswith('.') or entry in ['__pycache__', 'node_modules']:
                            continue
                            
                        child_path = os.path.join(path, entry)
                        child_item = build_tree(child_path, max_depth, current_depth + 1)
                        children.append(child_item)
                        total_size += child_item.get("size", 0)
                    
                    item["children"] = children
                    item["children_count"] = len(children)
                    item["size"] = total_size
                    
                except PermissionError:
                    item["error"] = "权限不足"
                    item["children"] = []
                    item["children_count"] = 0
                    
            else:
                # 文件
                try:
                    stat = os.stat(path)
                    item["size"] = stat.st_size
                    item["mime_type"] = mimetypes.guess_type(path)[0] or "application/octet-stream"
                    
                    # 判断是否为文本文件
                    item["is_text"] = self._is_text_file(path, item["mime_type"])
                    
                except (OSError, IOError):
                    item["size"] = 0
                    item["error"] = "无法读取文件信息"
            
            return item

        return build_tree(dir_path)

    async def _get_file_info(self, file_path):
        """获取单个文件信息"""
        try:
            stat = os.stat(file_path)
            mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            
            return {
                "name": os.path.basename(file_path),
                "type": "file",
                "path": "",
                "size": stat.st_size,
                "modified_time": stat.st_mtime,
                "mime_type": mime_type,
                "is_text": self._is_text_file(file_path, mime_type)
            }
        except Exception as e:
            logger.error(f"获取文件信息失败: {e}")
            return {
                "name": os.path.basename(file_path),
                "type": "file", 
                "path": "",
                "size": 0,
                "error": str(e)
            }

    def _is_text_file(self, file_path, mime_type):
        """判断是否为文本文件"""
        # 基于MIME类型判断
        if mime_type:
            if mime_type.startswith('text/'):
                return True
            if mime_type in ['application/json', 'application/xml', 'application/javascript']:
                return True
        
        # 基于文件扩展名判断
        text_extensions = {
            '.txt', '.md', '.py', '.js', '.html', '.css', '.json', '.xml', '.yml', '.yaml',
            '.sql', '.sh', '.bat', '.cfg', '.conf', '.ini', '.log', '.csv', '.tsv',
            '.java', '.cpp', '.c', '.h', '.php', '.rb', '.go', '.rs', '.scala',
            '.dockerfile', '.gitignore', '.env', '.toml', '.requirements'
        }
        
        ext = Path(file_path).suffix.lower()
        if ext in text_extensions:
            return True
        
        # 尝试读取文件头部判断
        try:
            with open(file_path, 'rb') as f:
                chunk = f.read(1024)
                # 检查是否包含null字节（二进制文件的特征）
                if b'\x00' in chunk:
                    return False
                # 尝试UTF-8解码
                chunk.decode('utf-8')
                return True
        except (UnicodeDecodeError, IOError):
            return False

    async def get_file_content(self, file_path, relative_path = ""):
        """
        获取文件内容
        
        Args:
            file_path: 基础文件路径
            relative_path: 相对路径（用于解压目录中的特定文件）
            
        Returns:
            文件内容信息
        """
        try:
            # 构建完整路径
            if relative_path:
                full_base_path = file_storage_service.get_file_path(file_path)
                full_file_path = os.path.join(full_base_path, relative_path)
            else:
                full_file_path = file_storage_service.get_file_path(file_path)
            
            # 安全检查：确保路径在允许范围内
            full_base_path = file_storage_service.get_file_path(file_path)
            if not os.path.commonpath([full_file_path, full_base_path]).startswith(full_base_path):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="路径访问被拒绝"
                )
            
            if not os.path.exists(full_file_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="文件不存在"
                )
            
            if not os.path.isfile(full_file_path):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="指定路径不是文件"
                )
            
            # 获取文件信息
            stat = os.stat(full_file_path)
            mime_type = mimetypes.guess_type(full_file_path)[0] or "application/octet-stream"
            is_text = self._is_text_file(full_file_path, mime_type)
            
            result = {
                "name": os.path.basename(full_file_path),
                "path": relative_path,
                "size": stat.st_size,
                "modified_time": stat.st_mtime,
                "mime_type": mime_type,
                "is_text": is_text
            }
            
            # 如果是文本文件且大小合理，读取内容
            if is_text and stat.st_size <= 1024 * 1024:  # 限制1MB
                try:
                    with open(full_file_path, 'r', encoding='utf-8') as f:
                        result["content"] = f.read()
                except UnicodeDecodeError:
                    # 尝试其他编码
                    try:
                        with open(full_file_path, 'r', encoding='gbk') as f:
                            result["content"] = f.read()
                        result["encoding"] = "gbk"
                    except UnicodeDecodeError:
                        result["content"] = "文件编码不支持预览"
                        result["error"] = "编码错误"
                except Exception as e:
                    result["content"] = f"读取文件失败: {str(e)}"
                    result["error"] = str(e)
            elif stat.st_size > 1024 * 1024:
                result["content"] = "文件过大，不支持在线预览"
                result["too_large"] = True
            else:
                result["content"] = "二进制文件，不支持文本预览"
                result["binary"] = True
            
            return result
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取文件内容失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"获取文件内容失败: {str(e)}"
            )

    async def download_file(self, file_path, relative_path = ""):
        """
        下载文件
        
        Args:
            file_path: 基础文件路径
            relative_path: 相对路径（可选）
            
        Returns:
            文件响应
        """
        try:
            # 构建完整路径
            if relative_path:
                full_base_path = file_storage_service.get_file_path(file_path)
                full_file_path = os.path.join(full_base_path, relative_path)
                filename = os.path.basename(full_file_path)
            else:
                full_file_path = file_storage_service.get_file_path(file_path)
                filename = os.path.basename(full_file_path)
            
            # 安全检查
            full_base_path = file_storage_service.get_file_path(file_path)
            if relative_path and not os.path.commonpath([full_file_path, full_base_path]).startswith(full_base_path):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="路径访问被拒绝"
                )
            
            if not os.path.exists(full_file_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="文件不存在"
                )
            
            if not os.path.isfile(full_file_path):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="指定路径不是文件"
                )
            
            # 获取MIME类型
            mime_type = mimetypes.guess_type(full_file_path)[0] or "application/octet-stream"
            
            return FileResponse(
                path=full_file_path,
                filename=filename,
                media_type=mime_type
            )
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"下载文件失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"下载文件失败: {str(e)}"
            )


# 创建项目文件服务实例
project_file_service = ProjectFileService()
