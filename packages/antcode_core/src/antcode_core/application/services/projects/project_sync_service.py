"""项目智能同步服务"""

import os
import zipfile
from datetime import datetime

from fastapi import HTTPException, status
from loguru import logger

from antcode_core.common.hash_utils import create_hash_calculator
from antcode_core.domain.models import Project


class ProjectSyncService:
    """项目同步服务"""

    def __init__(self):
        self.chunk_size = 8 * 1024 * 1024

    async def get_project_transfer_info(
        self,
        project_id,
        project: Project | None = None,
    ):
        """
        获取传输策略

        新架构要求：所有文件项目必须存储在 S3，Worker 通过预签名 URL 下载

        策略选择:
        - 代码项目 → 代码内容
        - S3 项目目录 → 打包下载或预签名 URL
        """
        from antcode_core.application.services.projects.relation_service import relation_service
        from antcode_core.infrastructure.storage.presign import is_s3_storage_enabled

        if project is None:
            project = await Project.get_or_none(id=project_id)

        if not project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

        # 代码项目直接返回代码内容（不走文件传输）
        if project.type.value == "code":
            code_detail = await relation_service.get_project_code_detail(project_id)
            if not code_detail:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="代码项目详情不存在"
                )
            return {
                "transfer_method": "code",
                "content": code_detail.content,
                "language": code_detail.language,
                "entry_point": code_detail.entry_point,
            }

        # 文件项目 - 新架构强制要求 S3
        if not is_s3_storage_enabled():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="文件项目需要 S3 存储后端，请配置 FILE_STORAGE_BACKEND=s3",
            )

        file_detail = await relation_service.get_project_file_detail(project_id)
        if not file_detail:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目文件信息不存在")

        # 所有文件项目都通过 S3 传输
        return await self._get_s3_project_transfer_info(project, file_detail)

    async def _get_s3_project_transfer_info(self, project, file_detail):
        """获取 S3 项目的传输信息

        根据文件类型选择不同的传输策略：
        - 单个文件（非压缩）：直接生成预签名 URL
        - 压缩包：使用原始压缩包或重新打包
        - 目录：打包后传输
        """
        from antcode_core.infrastructure.storage.base import get_file_storage_backend
        from antcode_core.infrastructure.storage.presign import try_generate_download_url
        from antcode_core.infrastructure.storage.s3_client import get_s3_client_manager

        backend = get_file_storage_backend()
        s3_manager = get_s3_client_manager()
        file_path = file_detail.file_path

        # 情况1：单个文件（非压缩包）- 直接生成预签名 URL
        if not file_detail.is_compressed:
            # 检查文件是否存在
            if await backend.exists(file_path):
                presigned_url = await try_generate_download_url(file_path, expires_in=3600)

                logger.info(f"使用 S3 单文件直传 [{project.name}]: {file_path}")
                return {
                    "transfer_method": "s3_single_file",
                    "file_path": file_path,
                    "file_size": file_detail.file_size,
                    "file_hash": file_detail.file_hash,
                    "is_compressed": False,
                    "original_name": file_detail.original_name,
                    "entry_point": file_detail.entry_point,
                    "modified": False,
                    "presigned_url": presigned_url,
                }
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"项目文件不存在: {file_path}",
                )

        # 情况2：检查是否有原始压缩包且未修改
        if file_detail.original_file_path:
            is_modified = await self._check_s3_project_modified(file_detail)

            if not is_modified:
                original_path = file_detail.original_file_path
                if await backend.exists(original_path):
                    presigned_url = await try_generate_download_url(original_path, expires_in=3600)

                    logger.info(f"使用 S3 原始压缩包 [{project.name}]")
                    return {
                        "transfer_method": "s3_original",
                        "file_path": original_path,
                        "file_size": file_detail.file_size,
                        "file_hash": file_detail.file_hash,
                        "is_compressed": True,
                        "original_name": file_detail.original_name,
                        "entry_point": file_detail.entry_point,
                        "modified": False,
                        "presigned_url": presigned_url,
                    }

        # 情况3：需要打包 S3 项目目录
        logger.info(f"打包 S3 项目目录 [{project.name}]")
        pack_info = await self._pack_s3_project(project, file_detail, backend, s3_manager)

        return {
            "transfer_method": "s3_pack",
            **pack_info,
            "modified": True,
        }

    async def _check_s3_project_modified(self, file_detail) -> bool:
        """检查 S3 项目是否被修改

        通过比较项目目录的文件列表哈希来判断
        """
        # TODO: 实现更精确的 S3 项目修改检测
        # 目前简单返回 False，假设未修改
        return False

    async def _pack_s3_project(self, project, file_detail, backend, s3_manager):
        """打包 S3 项目目录为压缩文件

        1. 下载 S3 项目目录到临时目录
        2. 打包为 zip 文件
        3. 上传到 S3 临时目录
        4. 返回下载信息
        """
        import tempfile

        from antcode_core.infrastructure.storage.presign import try_generate_download_url

        s3_prefix = file_detail.file_path
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        pack_filename = f"{project.public_id}_{timestamp}.zip"
        s3_pack_key = f"temp/{pack_filename}"

        with tempfile.TemporaryDirectory() as temp_dir:
            # 1. 下载 S3 项目目录
            download_dir = os.path.join(temp_dir, "project")
            os.makedirs(download_dir, exist_ok=True)

            try:
                downloaded = await s3_manager.download_to_directory(
                    bucket=backend.bucket,
                    prefix=s3_prefix.rstrip("/") + "/",
                    local_dir=download_dir,
                )
                logger.debug(f"从 S3 下载项目目录: {s3_prefix}, 共 {downloaded} 个文件")
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"下载 S3 项目目录失败: {e}",
                )

            # 2. 打包为 zip 文件
            pack_path = os.path.join(temp_dir, pack_filename)
            await self._compress_directory(download_dir, pack_path)

            # 3. 计算文件信息
            file_hash, file_size = await self._calculate_file_info(pack_path)

            # 4. 上传到 S3 临时目录
            try:
                with open(pack_path, "rb") as f:
                    client = await s3_manager.get_client()
                    await client.put_object(
                        Bucket=backend.bucket,
                        Key=s3_pack_key,
                        Body=f.read(),
                    )
                logger.info(f"项目打包上传到 S3: {s3_pack_key}, 大小: {file_size} 字节")
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"上传打包文件到 S3 失败: {e}",
                )

        # 5. 生成预签名 URL
        presigned_url = await try_generate_download_url(s3_pack_key, expires_in=3600)

        return {
            "file_path": s3_pack_key,
            "file_size": file_size,
            "file_hash": file_hash,
            "original_name": file_detail.original_name or pack_filename,
            "entry_point": file_detail.entry_point,
            "is_compressed": True,
            "is_temporary": True,
            "presigned_url": presigned_url,
        }

    async def _compress_directory(self, source_dir, output_path):
        """压缩目录"""
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(source_dir):
                dirs.sort()
                files.sort()

                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, source_dir)
                    zipf.write(file_path, arcname)

        logger.info(f"压缩完成: {source_dir}")

    async def _calculate_file_info(self, file_path):
        """计算文件信息"""
        hasher = create_hash_calculator("md5")
        file_size = 0

        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
                file_size += len(chunk)

        return hasher.hexdigest(), file_size

    async def cleanup_temporary_file(self, file_path):
        """清理临时文件（S3）"""
        try:
            if file_path.startswith("temp/"):
                from antcode_core.infrastructure.storage.base import get_file_storage_backend

                backend = get_file_storage_backend()
                if await backend.exists(file_path):
                    await backend.delete(file_path)
                    logger.info(f"已清理 S3 临时文件: {file_path}")
        except Exception as e:
            logger.warning(f"清理临时文件失败: {e}")

    async def get_incremental_changes(self, project_id, client_file_hashes):
        """计算增量变更"""
        from antcode_core.application.services.projects.relation_service import relation_service

        project = await Project.get_or_none(id=project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")

        file_detail = await relation_service.get_project_file_detail(project_id)
        if not file_detail or not file_detail.is_compressed:
            return {"error": "仅支持压缩项目的增量同步"}

        return await self._get_s3_incremental_changes(project, file_detail, client_file_hashes)

    async def _get_s3_incremental_changes(self, project, file_detail, client_file_hashes):
        """计算 S3 项目的增量变更"""
        import hashlib

        from antcode_core.infrastructure.storage.base import get_file_storage_backend
        from antcode_core.infrastructure.storage.s3_client import get_s3_client_manager

        backend = get_file_storage_backend()
        s3_manager = get_s3_client_manager()
        s3_prefix = file_detail.file_path.rstrip("/") + "/"

        try:
            # 列出 S3 前缀下的所有对象
            objects = await s3_manager.list_objects(
                bucket=backend.bucket,
                prefix=s3_prefix,
                max_keys=2000,
            )

            server_files = {}
            prefix_len = len(s3_prefix)

            for obj in objects:
                key = obj["key"]
                relative_path = key[prefix_len:]
                if not relative_path:
                    continue

                # 使用 ETag 作为哈希（S3 的 ETag 通常是 MD5）
                # 注意：对于分片上传的文件，ETag 不是简单的 MD5
                # 这里我们下载文件内容计算 MD5
                try:
                    file_bytes = await backend.get_file_bytes(key)
                    hasher = hashlib.md5()
                    hasher.update(file_bytes)
                    server_files[relative_path] = hasher.hexdigest()
                except Exception as e:
                    logger.warning(f"计算 S3 文件哈希失败: {key}, 错误: {e}")
                    continue

            client_paths = set(client_file_hashes.keys())
            server_paths = set(server_files.keys())

            added = list(server_paths - client_paths)
            deleted = list(client_paths - server_paths)
            common = server_paths & client_paths

            modified = [path for path in common if server_files[path] != client_file_hashes[path]]
            unchanged = [path for path in common if server_files[path] == client_file_hashes[path]]

            logger.info(
                f"S3 增量差异 [{project.name}] "
                f"+{len(added)} ~{len(modified)} -{len(deleted)} ={len(unchanged)}"
            )

            return {
                "added": added,
                "modified": modified,
                "deleted": deleted,
                "unchanged": unchanged,
                "server_files": server_files,
            }

        except Exception as e:
            logger.error(f"计算 S3 增量变更失败: {e}")
            return {"error": f"计算增量变更失败: {str(e)}"}


project_sync_service = ProjectSyncService()
