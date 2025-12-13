"""项目智能同步服务"""

import os
import zipfile
from typing import Dict, Any, Tuple
from datetime import datetime

from loguru import logger
from fastapi import HTTPException, status

from src.core.config import settings
from src.models import Project, ProjectFile
from src.services.files.file_storage import file_storage_service
from src.utils.hash_utils import create_hash_calculator


class ProjectSyncService:
    """项目同步服务"""

    def __init__(self):
        self.storage_root = settings.LOCAL_STORAGE_PATH
        self.chunk_size = 8 * 1024 * 1024
        self.temp_dir = os.path.join(self.storage_root, "temp")
        os.makedirs(self.temp_dir, exist_ok=True)

    async def get_project_transfer_info(
        self, 
        project_id: int
    ) -> Dict[str, Any]:
        """
        获取传输策略
        
        策略选择:
        - 未修改压缩项目 → 原始文件
        - 已修改压缩项目 → 重新打包
        - 单文件项目 → 直接传输
        - 代码项目 → 代码内容
        """
        from src.services.projects.relation_service import relation_service

        project = await Project.get_or_none(id=project_id)

        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目不存在"
            )

        # 代码项目直接返回代码内容（不走文件传输）
        if project.type.value == "code":
            code_detail = await relation_service.get_project_code_detail(project_id)
            if not code_detail:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="代码项目详情不存在"
                )
            return {
                "transfer_method": "code",
                "content": code_detail.content,
                "language": code_detail.language,
                "entry_point": code_detail.entry_point,
            }

        # 文件项目
        file_detail = await relation_service.get_project_file_detail(project_id)
        if not file_detail:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目文件信息不存在"
            )

        is_modified = await self._check_project_modified(file_detail)

        if file_detail.is_compressed:
            if not is_modified:
                original_path = file_detail.original_file_path or file_detail.file_path
                full_path = file_storage_service.get_file_path(original_path)

                if os.path.exists(full_path):
                    logger.info(f"使用原始文件 [{project.name}]")
                    return {
                        "transfer_method": "original",
                        "file_path": original_path,
                        "file_size": file_detail.file_size,
                        "file_hash": file_detail.file_hash,
                        "is_compressed": True,
                        "original_name": file_detail.original_name,
                        "entry_point": file_detail.entry_point,
                        "modified": False,
                    }

            logger.info(f"需要重新打包 [{project.name}]")
            repack_info = await self._repack_modified_project(project, file_detail)
            return {
                "transfer_method": "repack",
                **repack_info,
                "modified": True,
            }
        else:
            return {
                "transfer_method": "direct",
                "file_path": file_detail.file_path,
                "file_size": file_detail.file_size,
                "file_hash": file_detail.file_hash,
                "is_compressed": False,
                "original_name": file_detail.original_name,
                "entry_point": file_detail.entry_point,
                "modified": False,
            }

    async def _check_project_modified(self, file_detail: ProjectFile) -> bool:
        """检测项目修改状态"""
        if hasattr(file_detail, 'is_modified') and file_detail.is_modified:
            return True

        if file_detail.is_compressed:
            extracted_path = file_storage_service.get_file_path(file_detail.file_path)

            if not os.path.exists(extracted_path) or not os.path.isdir(extracted_path):
                return False

            try:
                current_hash = await self._calculate_directory_hash(extracted_path)

                if hasattr(file_detail, 'extracted_hash') and file_detail.extracted_hash:
                    return current_hash != file_detail.extracted_hash

                return True
            except Exception as e:
                logger.warning(f"Hash计算失败: {e}")
                return True

        return False

    async def _calculate_directory_hash(self, directory: str) -> str:
        """计算目录hash"""
        hasher = create_hash_calculator("md5")

        file_paths = []
        for root, dirs, files in os.walk(directory):
            dirs.sort()
            files.sort()
            for file in files:
                file_path = os.path.join(root, file)
                file_paths.append(file_path)

        for file_path in file_paths:
            try:
                rel_path = os.path.relpath(file_path, directory)
                hasher.update(rel_path.encode('utf-8'))

                with open(file_path, 'rb') as f:
                    while chunk := f.read(8192):
                        hasher.update(chunk)
            except Exception as e:
                logger.warning(f"文件读取失败 {file_path}: {e}")
                continue

        return hasher.hexdigest()

    async def _repack_modified_project(
        self, 
        project: Project, 
        file_detail: ProjectFile
    ) -> Dict[str, Any]:
        """重新打包已修改项目"""
        extracted_path = file_storage_service.get_file_path(file_detail.file_path)

        if not os.path.exists(extracted_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="解压目录不存在"
            )

        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        temp_name = f"{project.public_id}_{timestamp}.zip"
        temp_path = os.path.join(self.temp_dir, temp_name)

        try:
            await self._compress_directory(extracted_path, temp_path)

            file_hash, file_size = await self._calculate_file_info(temp_path)

            relative_path = os.path.relpath(temp_path, self.storage_root)

            logger.info(f"重新打包完成 [{project.name}] {file_size}字节")

            return {
                "file_path": relative_path,
                "file_size": file_size,
                "file_hash": file_hash,
                "original_name": file_detail.original_name,
                "entry_point": file_detail.entry_point,
                "is_compressed": True,
                "is_temporary": True,
            }
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"打包失败: {str(e)}"
            )

    async def _compress_directory(self, source_dir: str, output_path: str):
        """压缩目录"""
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(source_dir):
                dirs.sort()
                files.sort()

                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, source_dir)
                    zipf.write(file_path, arcname)

        logger.info(f"压缩完成: {source_dir}")

    async def _calculate_file_info(self, file_path: str) -> Tuple[str, int]:
        """计算文件信息"""
        hasher = create_hash_calculator("md5")
        file_size = 0

        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
                file_size += len(chunk)

        return hasher.hexdigest(), file_size

    async def cleanup_temporary_file(self, file_path: str):
        """清理临时文件"""
        try:
            full_path = file_storage_service.get_file_path(file_path)
            if "temp/" in full_path and os.path.exists(full_path):
                os.remove(full_path)
                logger.info(f"已清理: {full_path}")
        except Exception as e:
            logger.warning(f"清理失败: {e}")

    async def get_incremental_changes(
        self, 
        project_id: int,
        client_file_hashes: Dict[str, str]
    ) -> Dict[str, Any]:
        """计算增量变更"""
        from src.services.projects.relation_service import relation_service

        project = await Project.get_or_none(id=project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")

        file_detail = await relation_service.get_project_file_detail(project_id)
        if not file_detail or not file_detail.is_compressed:
            return {"error": "仅支持压缩项目的增量同步"}

        extracted_path = file_storage_service.get_file_path(file_detail.file_path)
        if not os.path.exists(extracted_path):
            return {"error": "项目解压目录不存在"}

        server_files = {}
        for root, dirs, files in os.walk(extracted_path):
            dirs.sort()
            files.sort()
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, extracted_path)

                hasher = create_hash_calculator("md5")
                with open(file_path, 'rb') as f:
                    hasher.update(f.read())
                server_files[rel_path] = hasher.hexdigest()

        client_paths = set(client_file_hashes.keys())
        server_paths = set(server_files.keys())

        added = list(server_paths - client_paths)
        deleted = list(client_paths - server_paths)
        common = server_paths & client_paths

        modified = [
            path for path in common
            if server_files[path] != client_file_hashes[path]
        ]
        unchanged = [
            path for path in common
            if server_files[path] == client_file_hashes[path]
        ]

        logger.info(
            f"增量差异 [{project.name}] "
            f"+{len(added)} ~{len(modified)} -{len(deleted)} ={len(unchanged)}"
        )

        return {
            "added": added,
            "modified": modified,
            "deleted": deleted,
            "unchanged": unchanged,
            "server_files": server_files,
        }


project_sync_service = ProjectSyncService()

