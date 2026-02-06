"""
项目版本服务

管理项目文件的不可变版本：
- 发布草稿为新版本
- 构建 artifact.zip
- 版本列表与回滚
- 丢弃草稿
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from datetime import datetime

from fastapi import HTTPException, status
from loguru import logger

from antcode_core.domain.models import ProjectFile, ProjectFileVersion
from antcode_core.application.services.projects.draft_service import (
    DraftManifest,
    project_draft_service,
)
from antcode_core.infrastructure.storage.base import get_file_storage_backend
from antcode_core.infrastructure.storage.s3_client import get_s3_client_manager


class VersionManifest:
    """版本 manifest 数据结构"""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        project_id: int,
        version: int,
        version_id: str,
        files: list[dict],
        created_at: str,
        content_hash: str,
    ):
        self.schema = self.SCHEMA_VERSION
        self.project_id = project_id
        self.kind = "version"
        self.version = version
        self.version_id = version_id
        self.files = files
        self.created_at = created_at
        self.content_hash = content_hash

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_size(self) -> int:
        return sum(f.get("size", 0) for f in self.files)

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "project_id": self.project_id,
            "kind": self.kind,
            "version": self.version,
            "version_id": self.version_id,
            "created_at": self.created_at,
            "content_hash": self.content_hash,
            "file_count": self.file_count,
            "total_size": self.total_size,
            "files": self.files,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class ProjectVersionService:
    """项目版本服务"""

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

    def _get_version_prefix(self, project_id: int, version: int) -> str:
        """获取版本目录前缀"""
        return f"projects/{project_id}/versions/v{version}/"

    def _get_version_manifest_key(self, project_id: int, version: int) -> str:
        """获取版本 manifest 路径"""
        return f"projects/{project_id}/versions/v{version}/manifest.json"

    def _get_version_artifact_key(self, project_id: int, version: int) -> str:
        """获取版本 artifact 路径"""
        return f"projects/{project_id}/versions/v{version}/artifact.zip"

    def _get_draft_prefix(self, project_id: int) -> str:
        """获取草稿文件前缀"""
        return f"projects/{project_id}/draft/files/"

    async def publish(
        self,
        project_file: ProjectFile,
        description: str | None = None,
        user_id: int | None = None,
    ) -> ProjectFileVersion:
        """
        发布草稿为新版本

        1. 冻结当前 draft manifest
        2. 构建 artifact.zip
        3. 上传到 versions/{version}/
        4. 创建 ProjectFileVersion 记录
        5. 更新 ProjectFile.published_version
        """
        # 获取当前草稿 manifest
        draft_manifest = await project_draft_service.get_manifest(project_file)

        if not draft_manifest.files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="草稿为空，无法发布",
            )

        # 计算新版本号
        new_version = (project_file.published_version or 0) + 1
        version_id = f"vf_{uuid.uuid4().hex[:16]}"

        # 构建 artifact.zip
        artifact_data = await self._build_artifact(project_file, draft_manifest)

        # 计算 artifact 哈希
        artifact_hash = f"sha256:{hashlib.sha256(artifact_data).hexdigest()}"

        # 上传 artifact
        artifact_key = self._get_version_artifact_key(project_file.project_id, new_version)
        try:
            client = await self.s3_manager.get_client()
            await client.put_object(
                Bucket=self.backend.bucket,
                Key=artifact_key,
                Body=artifact_data,
                ContentType="application/zip",
            )
            logger.info(f"artifact 已上传: {artifact_key}")
        except Exception as e:
            logger.error(f"上传 artifact 失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"上传 artifact 失败: {e}",
            )

        # 创建版本 manifest
        version_manifest = VersionManifest(
            project_id=project_file.project_id,
            version=new_version,
            version_id=version_id,
            files=draft_manifest.files,
            created_at=datetime.now().isoformat(),
            content_hash=draft_manifest.content_hash,
        )

        # 上传版本 manifest
        manifest_key = self._get_version_manifest_key(project_file.project_id, new_version)
        try:
            await client.put_object(
                Bucket=self.backend.bucket,
                Key=manifest_key,
                Body=version_manifest.to_json().encode("utf-8"),
                ContentType="application/json",
            )
            logger.info(f"版本 manifest 已上传: {manifest_key}")
        except Exception as e:
            logger.error(f"上传版本 manifest 失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"上传版本 manifest 失败: {e}",
            )

        # 创建版本记录
        version_record = await ProjectFileVersion.create(
            project_id=project_file.project_id,
            version=new_version,
            version_id=version_id,
            manifest_key=manifest_key,
            artifact_key=artifact_key,
            content_hash=draft_manifest.content_hash,
            file_count=draft_manifest.file_count,
            total_size=draft_manifest.total_size,
            created_by=user_id,
            description=description,
        )

        # 更新 ProjectFile
        project_file.published_version = new_version
        project_file.dirty = False
        project_file.dirty_files_count = 0
        project_file.file_hash = artifact_hash
        await project_file.save()

        logger.info(
            f"项目 {project_file.project_id} 已发布版本 v{new_version}, "
            f"version_id={version_id}, files={draft_manifest.file_count}"
        )

        return version_record

    async def _build_artifact(
        self, project_file: ProjectFile, manifest: DraftManifest
    ) -> bytes:
        """构建 artifact.zip"""
        draft_prefix = self._get_draft_prefix(project_file.project_id)

        # 创建内存中的 zip
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            client = await self.s3_manager.get_client()

            for file_info in manifest.files:
                path = file_info["path"]
                s3_key = f"{draft_prefix}{path}"

                try:
                    response = await client.get_object(
                        Bucket=self.backend.bucket,
                        Key=s3_key,
                    )
                    async with response["Body"] as stream:
                        content = await stream.read()

                    zf.writestr(path, content)
                except Exception as e:
                    logger.warning(f"读取文件失败: {s3_key}, error={e}")
                    # 继续处理其他文件

        zip_buffer.seek(0)
        return zip_buffer.read()

    async def discard(self, project_file: ProjectFile, user_id: int | None = None):
        """
        丢弃草稿修改，恢复到最新已发布版本
        """
        published_version = project_file.published_version or 0

        if published_version == 0:
            # 没有已发布版本，清空草稿
            await self._clear_draft(project_file)
            project_file.dirty = False
            project_file.dirty_files_count = 0
            await project_file.save()
            logger.info(f"项目 {project_file.project_id} 草稿已清空（无已发布版本）")
            return

        # 从已发布版本恢复草稿
        version_record = await ProjectFileVersion.get_or_none(
            project_id=project_file.project_id,
            version=published_version,
        )

        if not version_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"版本 v{published_version} 不存在",
            )

        await self._restore_draft_from_version(project_file, version_record)

        project_file.dirty = False
        project_file.dirty_files_count = 0
        await project_file.save()

        logger.info(f"项目 {project_file.project_id} 草稿已恢复到 v{published_version}")

    async def rollback(
        self,
        project_file: ProjectFile,
        target_version: int,
        user_id: int | None = None,
    ):
        """
        回滚到指定版本（创建新草稿，不修改历史版本）
        """
        version_record = await ProjectFileVersion.get_or_none(
            project_id=project_file.project_id,
            version=target_version,
        )

        if not version_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"版本 v{target_version} 不存在",
            )

        await self._restore_draft_from_version(project_file, version_record)

        # 标记草稿为 dirty（因为与 latest 不同）
        project_file.dirty = True
        project_file.last_editor_id = user_id
        project_file.last_edit_at = datetime.now()
        await project_file.save()

        logger.info(f"项目 {project_file.project_id} 已回滚到 v{target_version}")

    async def _restore_draft_from_version(
        self, project_file: ProjectFile, version_record: ProjectFileVersion
    ):
        """从版本恢复草稿"""
        # 读取版本 manifest
        try:
            content = await self.backend.get_file_bytes(version_record.manifest_key)
            version_manifest = json.loads(content.decode("utf-8"))
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"读取版本 manifest 失败: {e}",
            )

        # 清空当前草稿
        await self._clear_draft(project_file)

        # 解压 artifact 到草稿目录
        draft_prefix = self._get_draft_prefix(project_file.project_id)

        try:
            artifact_content = await self.backend.get_file_bytes(version_record.artifact_key)
            zip_buffer = io.BytesIO(artifact_content)

            client = await self.s3_manager.get_client()

            with zipfile.ZipFile(zip_buffer, "r") as zf:
                for name in zf.namelist():
                    if name.endswith("/"):
                        continue  # 跳过目录

                    content = zf.read(name)
                    s3_key = f"{draft_prefix}{name}"

                    await client.put_object(
                        Bucket=self.backend.bucket,
                        Key=s3_key,
                        Body=content,
                    )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"恢复草稿失败: {e}",
            )

        # 创建新的草稿 manifest
        draft_manifest = DraftManifest(
            project_id=project_file.project_id,
            files=version_manifest.get("files", []),
        )
        await project_draft_service.save_manifest(project_file, draft_manifest)

    async def _clear_draft(self, project_file: ProjectFile):
        """清空草稿目录"""
        draft_prefix = self._get_draft_prefix(project_file.project_id)

        try:
            objects = await self.s3_manager.list_objects(
                bucket=self.backend.bucket,
                prefix=draft_prefix,
                max_keys=2000,
            )

            if objects:
                client = await self.s3_manager.get_client()
                for obj in objects:
                    await client.delete_object(
                        Bucket=self.backend.bucket,
                        Key=obj["key"],
                    )

            # 删除 manifest
            manifest_key = f"projects/{project_file.project_id}/draft/manifest.json"
            await client.delete_object(
                Bucket=self.backend.bucket,
                Key=manifest_key,
            )
        except Exception as e:
            logger.warning(f"清空草稿失败: {e}")

    async def list_versions(self, project_id: int) -> list[dict]:
        """列出所有版本"""
        versions = await ProjectFileVersion.filter(project_id=project_id).order_by("-version")

        return [
            {
                "version": v.version,
                "version_id": v.version_id,
                "created_at": v.created_at.isoformat(),
                "created_by": v.created_by,
                "description": v.description,
                "file_count": v.file_count,
                "total_size": v.total_size,
                "content_hash": v.content_hash,
            }
            for v in versions
        ]

    async def get_version(self, project_id: int, version: int) -> ProjectFileVersion | None:
        """获取指定版本"""
        return await ProjectFileVersion.get_or_none(
            project_id=project_id,
            version=version,
        )

    async def get_latest_version(self, project_id: int) -> ProjectFileVersion | None:
        """获取最新版本"""
        return await ProjectFileVersion.filter(project_id=project_id).order_by("-version").first()


# 服务单例
project_version_service = ProjectVersionService()
