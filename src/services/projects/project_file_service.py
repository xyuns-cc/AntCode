"""
项目文件服务
处理项目文件的查看、下载、结构分析等功能
"""

import asyncio
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
        # 预览与编辑大小限制，避免加载或写入超大文件
        self.max_preview_size = 1024 * 1024  # 1MB
        self.max_edit_size = getattr(settings, "MAX_FILE_EDIT_SIZE", 1024 * 1024)

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

    async def _get_directory_structure(self, dir_path, max_depth=5):
        """
        获取目录结构
        
        Args:
            dir_path: 目录路径
            max_depth: 最大递归深度，防止目录过深导致性能问题
        """
        def build_tree(path, current_depth=0):
            """递归构建目录树"""
            # 达到最大深度，返回截断信息
            if current_depth >= max_depth:
                try:
                    children_count = len(os.listdir(path)) if os.path.isdir(path) else 0
                except (PermissionError, OSError):
                    children_count = 0
                    
                return {
                    "name": os.path.basename(path),
                    "type": "directory",
                    "path": os.path.relpath(path, dir_path),
                    "size": 0,
                    "children_count": children_count,
                    "truncated": True,
                    "message": f"目录深度超过{max_depth}层，已截断"
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
                file_count = 0
                dir_count = 0
                
                try:
                    entries = os.listdir(path)
                    
                    # 过滤和排序
                    # 排序：目录在前，文件在后，按名称排序
                    entries.sort(key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
                    
                    # 限制单个目录的子项数量，防止超大目录
                    max_children = 1000
                    if len(entries) > max_children:
                        item["truncated"] = True
                        item["total_children"] = len(entries)
                        item["message"] = f"目录包含{len(entries)}个项目，仅显示前{max_children}个"
                        entries = entries[:max_children]
                    
                    for entry in entries:
                        # 跳过隐藏文件、系统文件和大型依赖目录
                        if entry.startswith('.') or entry in ['__pycache__', 'node_modules', '.git', '.venv', 'venv', 'dist', 'build']:
                            continue
                            
                        child_path = os.path.join(path, entry)
                        
                        try:
                            child_item = build_tree(child_path, current_depth + 1)
                            children.append(child_item)
                            total_size += child_item.get("size", 0)
                            
                            if child_item.get("type") == "directory":
                                dir_count += 1
                            else:
                                file_count += 1
                        except (PermissionError, OSError) as e:
                            # 记录无法访问的项目
                            logger.warning(f"无法访问: {child_path}, error: {e}")
                            children.append({
                                "name": entry,
                                "type": "unknown",
                                "error": "权限不足或无法访问",
                                "path": os.path.relpath(child_path, dir_path)
                            })
                    
                    item["children"] = children
                    item["children_count"] = len(children)
                    item["file_count"] = file_count
                    item["dir_count"] = dir_count
                    item["size"] = total_size
                    
                except PermissionError:
                    item["error"] = "权限不足"
                    item["children"] = []
                    item["children_count"] = 0
                except Exception as e:
                    logger.error(f"读取目录失败: {path}, error: {e}")
                    item["error"] = f"读取失败: {str(e)}"
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
                    
                except (OSError, IOError) as e:
                    logger.warning(f"读取文件信息失败: {path}, error: {e}")
                    item["size"] = 0
                    item["error"] = "无法读取文件信息"
            
            return item

        try:
            return build_tree(dir_path)
        except Exception as e:
            logger.error(f"构建目录树失败: {dir_path}, error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"构建目录树失败: {str(e)}"
            )

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

    def _validate_and_resolve_path(self, base_path: str, relative_path: str = "") -> str:
        """
        验证并解析文件路径，防止路径遍历攻击
        
        Args:
            base_path: 基础路径
            relative_path: 相对路径
            
        Returns:
            安全的完整文件路径
            
        Raises:
            HTTPException: 路径不安全或不存在
        """
        # 规范化路径，移除 ../ 等
        if relative_path:
            relative_path = os.path.normpath(relative_path)
            # 防止路径遍历攻击
            if relative_path.startswith('..') or os.path.isabs(relative_path):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="非法路径：不允许访问父目录或使用绝对路径"
                )
        
        full_base_path = file_storage_service.get_file_path(base_path)
        full_base_path = os.path.normpath(full_base_path)
        
        # 判断base_path是文件还是目录
        if os.path.isfile(full_base_path):
            # 单个文件项目：直接使用base_path
            return full_base_path
        elif os.path.isdir(full_base_path):
            # 目录项目：需要relative_path
            if not relative_path:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="目录项目必须指定文件路径"
                )
            
            full_file_path = os.path.normpath(os.path.join(full_base_path, relative_path))
            
            # 安全检查：确保解析后的路径在允许范围内
            try:
                common = os.path.commonpath([full_file_path, full_base_path])
                if common != full_base_path:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="路径访问被拒绝：超出项目范围"
                    )
            except ValueError:
                # Windows下不同盘符会抛出ValueError
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="路径访问被拒绝：路径无效"
                )
            
            return full_file_path
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目文件路径不存在"
            )

    async def get_file_content(self, file_path, relative_path = ""):
        """
        获取文件内容
        
        Args:
            file_path: 基础文件路径（可能是单个文件或解压目录）
            relative_path: 相对路径（用于解压目录中的特定文件）
            
        Returns:
            文件内容信息
        """
        try:
            # 验证并解析路径
            full_file_path = self._validate_and_resolve_path(file_path, relative_path)
            
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
            file_size = stat.st_size
            mime_type = mimetypes.guess_type(full_file_path)[0] or "application/octet-stream"
            is_text = self._is_text_file(full_file_path, mime_type)
            
            result = {
                "name": os.path.basename(full_file_path),
                "path": relative_path,
                "size": file_size,
                "modified_time": stat.st_mtime,
                "mime_type": mime_type,
                "is_text": is_text
            }
            
            # 如果是文本文件且大小合理，读取内容
            if is_text and file_size <= self.max_preview_size:
                try:
                    # 尝试多种编码
                    encodings = ['utf-8', 'gbk', 'gb2312', 'latin-1']
                    content = None
                    used_encoding = None
                    
                    for encoding in encodings:
                        try:
                            with open(full_file_path, 'r', encoding=encoding) as f:
                                content = f.read()
                                used_encoding = encoding
                                break
                        except (UnicodeDecodeError, LookupError):
                            continue
                    
                    if content is not None:
                        result["content"] = content
                        result["encoding"] = used_encoding
                    else:
                        result["content"] = "文件编码不支持预览，请尝试下载文件"
                        result["error"] = "unsupported_encoding"
                        
                except Exception as e:
                    logger.error(f"读取文件失败: {full_file_path}, error: {e}")
                    result["content"] = f"读取文件失败: {str(e)}"
                    result["error"] = "read_error"
            elif file_size > self.max_preview_size:
                result["content"] = f"文件过大（{file_size / 1024 / 1024:.2f}MB），不支持在线预览，请下载查看"
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

    async def update_file_content(self, file_path, relative_path, content, encoding = "utf-8"):
        """
        更新文件内容
        
        Args:
            file_path: 基础文件路径（可能是单个文件或解压目录）
            relative_path: 相对路径（用于解压目录中的特定文件）
            content: 文件内容
            encoding: 文件编码
        """
        if content is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="文件内容不能为空"
            )

        try:
            # 验证并解析路径
            target_path = self._validate_and_resolve_path(file_path, relative_path)

            if not os.path.exists(target_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="文件不存在"
                )

            if not os.path.isfile(target_path):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="指定路径不是文件"
                )

            mime_type = mimetypes.guess_type(target_path)[0] or "application/octet-stream"

            if not self._is_text_file(target_path, mime_type):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="二进制文件不支持在线编辑"
                )

            # 验证编码
            try:
                encoded_bytes = content.encode(encoding or "utf-8")
            except (UnicodeEncodeError, LookupError) as encode_error:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"编码不受支持: {str(encode_error)}"
                ) from encode_error

            # 检查文件大小限制
            if len(encoded_bytes) > self.max_edit_size:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"文件内容超出在线编辑限制({self.max_edit_size / 1024:.0f}KB)"
                )

            # 创建备份（如果文件存在且有内容）
            backup_path = None
            if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
                backup_path = f"{target_path}.backup"
                try:
                    import shutil
                    shutil.copy2(target_path, backup_path)
                except Exception as e:
                    logger.warning(f"创建备份失败: {e}")

            # 异步写入文件
            def _write_file():
                with open(target_path, 'w', encoding=encoding or 'utf-8') as file_handle:
                    file_handle.write(content)

            try:
                await asyncio.to_thread(_write_file)
                
                # 写入成功，删除备份
                if backup_path and os.path.exists(backup_path):
                    try:
                        os.remove(backup_path)
                    except Exception as e:
                        logger.warning(f"删除备份失败: {e}")
                        
            except Exception as write_error:
                # 写入失败，尝试恢复备份
                if backup_path and os.path.exists(backup_path):
                    try:
                        import shutil
                        shutil.copy2(backup_path, target_path)
                        logger.info(f"已从备份恢复文件: {target_path}")
                    except Exception as restore_error:
                        logger.error(f"恢复备份失败: {restore_error}")
                raise write_error

            # 返回最新的文件内容信息
            return await self.get_file_content(file_path, relative_path or "")

        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"更新文件内容失败: {exc}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"更新文件内容失败: {str(exc)}"
            ) from exc

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
            # 验证并解析路径
            full_file_path = self._validate_and_resolve_path(file_path, relative_path)
            
            # 检查文件是否存在
            if not os.path.exists(full_file_path):
                logger.error(f"文件不存在: full_file_path={full_file_path}, file_path={file_path}, relative_path={relative_path}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="文件不存在"
                )
            
            # 检查是否是文件
            if not os.path.isfile(full_file_path):
                is_dir = os.path.isdir(full_file_path)
                logger.warning(f"尝试下载非文件路径: full_file_path={full_file_path}, is_directory={is_dir}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"指定路径是{'目录' if is_dir else '特殊文件'}，不支持下载。请选择具体文件"
                )
            
            # 获取文件名和MIME类型
            filename = os.path.basename(full_file_path)
            mime_type = mimetypes.guess_type(full_file_path)[0] or "application/octet-stream"
            
            logger.info(f"下载文件: {full_file_path}, size: {os.path.getsize(full_file_path)} bytes")
            
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
