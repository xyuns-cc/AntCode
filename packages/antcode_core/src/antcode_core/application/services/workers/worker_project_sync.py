"""节点项目同步服务 - 项目下载信息生成

从 worker_dispatcher.py 拆分，专注于项目下载信息生成（不再直连 Worker）。
"""

from __future__ import annotations

from loguru import logger

from antcode_core.domain.models import Project, Worker


class WorkerProjectSyncService:
    """节点项目同步服务"""

    async def sync_projects_to_worker(
        self, worker: Worker, project_ids: list[str]
    ) -> dict[str, list]:
        """批量同步项目到节点"""
        results, _ = await self.sync_projects_to_worker_with_info(worker, project_ids)
        return results

    async def sync_projects_to_worker_with_info(
        self, worker: Worker, project_ids: list[str]
    ) -> tuple[dict[str, list], dict[str, dict]]:
        """批量同步项目到节点，并返回项目下载信息

        新架构要求：Worker 只能通过 S3 预签名 URL 下载文件项目
        """
        from antcode_core.common.hash_utils import calculate_content_hash
        from antcode_core.application.services.projects.project_sync_service import project_sync_service
        from antcode_core.infrastructure.storage.presign import (
            is_s3_storage_enabled,
            try_generate_download_url,
        )

        # 新架构强制要求 S3 存储
        if not is_s3_storage_enabled():
            return (
                {"synced": [], "skipped": [], "failed": [
                    {"project_id": pid, "reason": "S3 存储未配置"} for pid in project_ids
                ]},
                {},
            )

        results: dict[str, list] = {"synced": [], "skipped": [], "failed": []}
        project_download_info: dict[str, dict] = {}

        project_map: dict[str, Project] = {}
        if project_ids:
            projects = await Project.filter(public_id__in=list(set(project_ids))).all()
            project_map = {p.public_id: p for p in projects}

        for project_id in project_ids:
            try:
                project = project_map.get(project_id)
                if not project:
                    results["failed"].append({"project_id": project_id, "reason": "项目不存在"})
                    continue

                transfer_info = await project_sync_service.get_project_transfer_info(
                    project.id,
                    project=project,
                )
                transfer_method = transfer_info.get("transfer_method")
                entry_point = transfer_info.get("entry_point")

                if transfer_method == "code":
                    # 代码项目：上传到 S3 并生成预签名 URL
                    content = transfer_info.get("content") or ""
                    content_bytes = content.encode("utf-8")
                    current_hash = calculate_content_hash(content_bytes, "md5")
                    relative_path = f"code/{project.public_id}/{entry_point or 'main.py'}"

                    from antcode_core.infrastructure.storage.base import get_file_storage_backend
                    backend = get_file_storage_backend()

                    try:
                        client = await backend._get_client()
                        await client.put_object(
                            Bucket=backend.bucket,
                            Key=relative_path,
                            Body=content_bytes,
                        )
                        logger.debug(f"代码项目已上传到 S3: {relative_path}")
                    except Exception as e:
                        logger.error(f"上传代码项目到 S3 失败: {e}")
                        results["failed"].append({"project_id": project_id, "reason": f"上传失败: {e}"})
                        continue

                    # 生成预签名 URL
                    presigned_url = await try_generate_download_url(relative_path, expires_in=3600)
                    if not presigned_url:
                        results["failed"].append({"project_id": project_id, "reason": "无法生成预签名 URL"})
                        continue

                    project_download_info[project_id] = {
                        "file_hash": current_hash,
                        "entry_point": entry_point or "",
                        "download_url": presigned_url,
                    }
                    results["synced"].append(project_id)

                elif transfer_method in ("s3_original", "s3_pack", "s3_single_file"):
                    # S3 项目：使用预签名 URL
                    file_path = transfer_info.get("file_path")
                    current_hash = transfer_info.get("file_hash")
                    presigned_url = transfer_info.get("presigned_url")
                    is_compressed = transfer_info.get("is_compressed", True)
                    original_name = transfer_info.get("original_name", "")

                    # 对于单文件项目，如果没有 entry_point，使用 original_name
                    if not entry_point and original_name and original_name.endswith(".py"):
                        entry_point = original_name

                    if not presigned_url:
                        # 尝试重新生成
                        presigned_url = await try_generate_download_url(file_path, expires_in=3600)

                    if presigned_url:
                        project_download_info[project_id] = {
                            "file_hash": current_hash or "",
                            "entry_point": entry_point or "",
                            "download_url": presigned_url,
                            "is_compressed": is_compressed,
                            "original_name": original_name,
                        }
                        results["synced"].append(project_id)
                    else:
                        results["failed"].append({"project_id": project_id, "reason": "无法生成 S3 预签名 URL"})

                else:
                    # 其他传输方式（original, repack, direct）- 需要从 S3 获取
                    file_path = transfer_info.get("file_path")
                    if not file_path:
                        results["failed"].append({"project_id": project_id, "reason": "项目文件路径为空"})
                        continue

                    current_hash = transfer_info.get("file_hash")

                    # 检查文件是否存在于 S3
                    from antcode_core.infrastructure.storage.base import get_file_storage_backend
                    backend = get_file_storage_backend()
                    if not await backend.exists(file_path):
                        results["failed"].append({"project_id": project_id, "reason": "项目文件不存在于 S3"})
                        continue

                    # 生成预签名 URL
                    presigned_url = await try_generate_download_url(file_path, expires_in=3600)
                    if not presigned_url:
                        results["failed"].append({"project_id": project_id, "reason": "无法生成预签名 URL"})
                        continue

                    project_download_info[project_id] = {
                        "file_hash": current_hash or "",
                        "entry_point": entry_point or "",
                        "download_url": presigned_url,
                    }
                    results["synced"].append(project_id)

            except Exception as e:
                logger.error(f"同步项目 {project_id} 失败: {e}")
                results["failed"].append({"project_id": project_id, "reason": str(e)})

        return results, project_download_info

    async def sync_single_project(
        self,
        worker: Worker,
        project: Project,
        transfer_info: dict,
        file_hash: str | None,
    ) -> bool:
        """同步单个项目到节点"""
        results, _ = await self.sync_projects_to_worker_with_info(worker, [project.public_id])
        return project.public_id in results.get("synced", []) or project.public_id in results.get(
            "skipped", []
        )

    async def sync_project_to_worker(
        self, worker: Worker, project_id: str, project_data: dict
    ) -> bool:
        """同步项目到节点"""
        results, _ = await self.sync_projects_to_worker_with_info(worker, [project_id])
        return project_id in results.get("synced", []) or project_id in results.get("skipped", [])


# 创建服务实例
worker_project_sync_service = WorkerProjectSyncService()
