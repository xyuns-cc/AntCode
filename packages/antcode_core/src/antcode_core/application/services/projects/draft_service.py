"""
项目草稿服务

管理项目文件的草稿工作区：
- 草稿初始化
- 文件 CRUD（带 ETag 并发控制）
- manifest 管理
- dirty 状态追踪
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from fastapi import HTTPException, status
from loguru import logger

from antcode_core.domain.models import ProjectFile
from antcode_core.infrastructure.storage.base import get_file_storage_backend
from antcode_core.infrastructure.storage.s3_client import get_s3_client_manager


class DraftManifest:
    """草稿 manifest 数据结构"""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        project_id: int,
        files: list[dict] | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ):
        self.schema = self.SCHEMA_VERSION
        self.project_id = project_id
        self.kind = "draft"
        self.version = None
        self.version_id = None
        self.files = files or []
        self.created_at = created_at or datetime.now().isoformat()
        self.updated_at = updated_at or datetime.now().isoformat()

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_size(self) -> int:
        return sum(f.get("size", 0) for f in self.files)

    @property
    def content_hash(self) -> str:
        """计算 manifest 内容哈希"""
        content = json.dumps(self.files, sort_keys=True, ensure_ascii=False)
        return f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "project_id": self.project_id,
            "kind": self.kind,
            "version": self.version,
            "version_id": self.version_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "content_hash": self.content_hash,
            "file_count": self.file_count,
            "total_size": self.total_size,
            "files": self.files,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> DraftManifest:
        manifest = cls(
            project_id=data.get("project_id", 0),
            files=data.get("files", []),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )
        return manifest

    def get_file(self, path: str) -> dict | None:
        """获取指定路径的文件信息"""
        for f in self.files:
            if f.get("path") == path:
                return f
        return None

    def set_file(self, path: str, size: int, file_hash: str, mtime: int | None = None):
        """设置/更新文件信息"""
        now = int(datetime.now().timestamp())
        file_info = {
            "path": path,
            "size": size,
            "hash": file_hash,
            "mtime": mtime or now,
            "mode": "0644",
        }

        # 查找并更新或添加
        for i, f in enumerate(self.files):
            if f.get("path") == path:
                self.files[i] = file_info
                self.updated_at = datetime.now().isoformat()
                return

        self.files.append(file_info)
        self.updated_at = datetime.now().isoformat()

    def remove_file(self, path: str) -> bool:
        """删除文件"""
        for i, f in enumerate(self.files):
            if f.get("path") == path:
                self.files.pop(i)
                self.updated_at = datetime.now().isoformat()
                return True
        return False

    def rename_file(self, old_path: str, new_path: str) -> bool:
        """重命名文件"""
        for f in self.files:
            if f.get("path") == old_path:
                f["path"] = new_path
                f["mtime"] = int(datetime.now().timestamp())
                self.updated_at = datetime.now().isoformat()
                return True
        return False


class ProjectDraftService:
    """项目草稿服务"""

    def __init__(self):
        self._backend = None
        self._s3_manager = None

    @property
    def backend(self):
        if self._backend is None:
            self._backend = get_file_storage_backend()
        return self._backend

    @property
    def s3_manager(self):
        if self._s3_manager is None:
            self._s3_manager = get_s3_client_manager()
        return self._s3_manager

    def _get_draft_prefix(self, project_id: int) -> str:
        """获取草稿文件前缀"""
        return f"projects/{project_id}/draft/files/"

    def _get_draft_manifest_key(self, project_id: int) -> str:
        """获取草稿 manifest 路径"""
        return f"projects/{project_id}/draft/manifest.json"

    async def get_manifest(self, project_file: ProjectFile) -> DraftManifest:
        """获取草稿 manifest"""
        manifest_key = project_file.draft_manifest_key
        if not manifest_key:
            manifest_key = self._get_draft_manifest_key(project_file.project_id)

        try:
            if await self.backend.exists(manifest_key):
                content = await self.backend.get_file_bytes(manifest_key)
                data = json.loads(content.decode("utf-8"))
                return DraftManifest.from_dict(data)
        except Exception as e:
            logger.warning(f"读取 manifest 失败: {manifest_key}, error={e}")

        # 返回空 manifest
        return DraftManifest(project_id=project_file.project_id)

    async def save_manifest(self, project_file: ProjectFile, manifest: DraftManifest):
        """保存草稿 manifest"""
        manifest_key = self._get_draft_manifest_key(project_file.project_id)

        try:
            client = await self.s3_manager.get_client()
            await client.put_object(
                Bucket=self.backend.bucket,
                Key=manifest_key,
                Body=manifest.to_json().encode("utf-8"),
                ContentType="application/json",
            )

            # 更新 ProjectFile
            project_file.draft_manifest_key = manifest_key
            project_file.draft_root_prefix = self._get_draft_prefix(project_file.project_id)
            await project_file.save()

            logger.debug(f"manifest 已保存: {manifest_key}")
        except Exception as e:
            logger.error(f"保存 manifest 失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"保存 manifest 失败: {e}",
            )

    async def get_file_content(
        self, project_file: ProjectFile, path: str
    ) -> tuple[bytes, str]:
        """
        获取草稿文件内容

        Returns:
            (content, etag)
        """
        prefix = self._get_draft_prefix(project_file.project_id)
        s3_key = f"{prefix}{path}"

        try:
            client = await self.s3_manager.get_client()
            response = await client.get_object(
                Bucket=self.backend.bucket,
                Key=s3_key,
            )

            etag = response.get("ETag", "").strip('"')
            async with response["Body"] as stream:
                content = await stream.read()

            return content, etag
        except Exception as e:
            if "NoSuchKey" in str(e) or "404" in str(e):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"文件不存在: {path}",
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"读取文件失败: {e}",
            )

    async def update_file_content(
        self,
        project_file: ProjectFile,
        path: str,
        content: bytes,
        expected_etag: str | None = None,
        user_id: int | None = None,
    ) -> str:
        """
        更新草稿文件内容（带 ETag 并发控制）

        Args:
            project_file: 项目文件记录
            path: 文件路径
            content: 文件内容
            expected_etag: 期望的 ETag（用于乐观锁）
            user_id: 操作用户 ID

        Returns:
            新的 ETag

        Raises:
            HTTPException 412: ETag 不匹配（并发冲突）
        """
        prefix = self._get_draft_prefix(project_file.project_id)
        s3_key = f"{prefix}{path}"

        # 并发控制：检查 ETag
        if expected_etag:
            try:
                client = await self.s3_manager.get_client()
                response = await client.head_object(
                    Bucket=self.backend.bucket,
                    Key=s3_key,
                )
                current_etag = response.get("ETag", "").strip('"')
                if current_etag and current_etag != expected_etag:
                    raise HTTPException(
                        status_code=status.HTTP_412_PRECONDITION_FAILED,
                        detail="文件已被其他用户修改，请刷新后重试",
                    )
            except HTTPException:
                raise
            except Exception:
                # 文件不存在，允许创建
                pass

        # 上传文件
        try:
            client = await self.s3_manager.get_client()
            response = await client.put_object(
                Bucket=self.backend.bucket,
                Key=s3_key,
                Body=content,
            )
            new_etag = response.get("ETag", "").strip('"')
        except Exception as e:
            logger.error(f"上传文件失败: {s3_key}, error={e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"保存文件失败: {e}",
            )

        # 更新 manifest
        manifest = await self.get_manifest(project_file)
        file_hash = f"sha256:{hashlib.sha256(content).hexdigest()}"
        manifest.set_file(path, len(content), file_hash)
        await self.save_manifest(project_file, manifest)

        # 更新 dirty 状态
        project_file.dirty = True
        project_file.dirty_files_count = (project_file.dirty_files_count or 0) + 1
        project_file.last_editor_id = user_id
        project_file.last_edit_at = datetime.now()
        await project_file.save()

        logger.info(f"文件已更新: {s3_key}, etag={new_etag}")
        return new_etag

    async def delete_file(
        self,
        project_file: ProjectFile,
        path: str,
        expected_etag: str | None = None,
        user_id: int | None = None,
    ):
        """删除草稿文件"""
        prefix = self._get_draft_prefix(project_file.project_id)
        s3_key = f"{prefix}{path}"

        # 并发控制
        if expected_etag:
            try:
                client = await self.s3_manager.get_client()
                response = await client.head_object(
                    Bucket=self.backend.bucket,
                    Key=s3_key,
                )
                current_etag = response.get("ETag", "").strip('"')
                if current_etag and current_etag != expected_etag:
                    raise HTTPException(
                        status_code=status.HTTP_412_PRECONDITION_FAILED,
                        detail="文件已被其他用户修改，请刷新后重试",
                    )
            except HTTPException:
                raise
            except Exception:
                pass

        # 删除文件
        try:
            client = await self.s3_manager.get_client()
            await client.delete_object(
                Bucket=self.backend.bucket,
                Key=s3_key,
            )
        except Exception as e:
            logger.error(f"删除文件失败: {s3_key}, error={e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"删除文件失败: {e}",
            )

        # 更新 manifest
        manifest = await self.get_manifest(project_file)
        manifest.remove_file(path)
        await self.save_manifest(project_file, manifest)

        # 更新 dirty 状态
        project_file.dirty = True
        project_file.last_editor_id = user_id
        project_file.last_edit_at = datetime.now()
        await project_file.save()

        logger.info(f"文件已删除: {s3_key}")

    async def move_file(
        self,
        project_file: ProjectFile,
        from_path: str,
        to_path: str,
        user_id: int | None = None,
    ):
        """移动/重命名文件"""
        prefix = self._get_draft_prefix(project_file.project_id)
        from_key = f"{prefix}{from_path}"
        to_key = f"{prefix}{to_path}"

        try:
            client = await self.s3_manager.get_client()

            # 复制到新位置
            await client.copy_object(
                Bucket=self.backend.bucket,
                CopySource={"Bucket": self.backend.bucket, "Key": from_key},
                Key=to_key,
            )

            # 删除原文件
            await client.delete_object(
                Bucket=self.backend.bucket,
                Key=from_key,
            )
        except Exception as e:
            if "NoSuchKey" in str(e):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"源文件不存在: {from_path}",
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"移动文件失败: {e}",
            )

        # 更新 manifest
        manifest = await self.get_manifest(project_file)
        manifest.rename_file(from_path, to_path)
        await self.save_manifest(project_file, manifest)

        # 更新 dirty 状态
        project_file.dirty = True
        project_file.last_editor_id = user_id
        project_file.last_edit_at = datetime.now()
        await project_file.save()

        logger.info(f"文件已移动: {from_path} -> {to_path}")

    async def get_edit_status(self, project_file: ProjectFile) -> dict:
        """获取编辑状态"""
        return {
            "dirty": project_file.dirty or False,
            "dirty_files_count": project_file.dirty_files_count or 0,
            "last_edit_at": project_file.last_edit_at.isoformat() if project_file.last_edit_at else None,
            "last_editor_id": project_file.last_editor_id,
            "published_version": project_file.published_version or 0,
        }


# 服务单例
project_draft_service = ProjectDraftService()
