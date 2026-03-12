"""节点项目同步服务。"""

from __future__ import annotations

from loguru import logger

from antcode_core.common.utils.worker_download import build_worker_download_url
from antcode_core.domain.models import Project, Worker


class WorkerProjectSyncService:
    """节点项目同步服务。"""

    async def sync_projects_to_worker(
        self,
        worker: Worker,
        project_ids: list[str],
    ) -> dict[str, list]:
        results, _ = await self.sync_projects_to_worker_with_info(worker, project_ids)
        return results

    async def sync_projects_to_worker_with_info(
        self,
        worker: Worker,
        project_ids: list[str],
    ) -> tuple[dict[str, list], dict[str, dict]]:
        """批量构建 Worker 项目下载信息。"""
        from antcode_core.application.services.projects.project_sync_service import (
            project_sync_service,
        )

        results: dict[str, list] = {"synced": [], "skipped": [], "failed": []}
        project_download_info: dict[str, dict] = {}
        project_map = await self._load_project_map(project_ids)

        for project_id in project_ids:
            try:
                project = project_map.get(project_id)
                if not project:
                    self._append_failure(results, project_id, "项目不存在")
                    continue

                transfer_info = await project_sync_service.get_project_transfer_info(
                    project.id,
                    project=project,
                )
                project_download_info[project_id] = self._build_download_info(
                    worker,
                    project_id,
                    transfer_info,
                )
                results["synced"].append(project_id)
            except Exception as exc:
                logger.error(f"同步项目 {project_id} 失败: {exc}")
                self._append_failure(results, project_id, str(exc))

        return results, project_download_info

    async def sync_single_project(
        self,
        worker: Worker,
        project: Project,
        transfer_info: dict,
        file_hash: str | None,
    ) -> bool:
        del transfer_info, file_hash
        results, _ = await self.sync_projects_to_worker_with_info(worker, [project.public_id])
        return self._is_synced(results, project.public_id)

    async def sync_project_to_worker(
        self,
        worker: Worker,
        project_id: str,
        project_data: dict,
    ) -> bool:
        del project_data
        results, _ = await self.sync_projects_to_worker_with_info(worker, [project_id])
        return self._is_synced(results, project_id)

    async def _load_project_map(self, project_ids: list[str]) -> dict[str, Project]:
        project_map: dict[str, Project] = {}
        for project_id in set(project_ids):
            project = await Project.get_or_none(public_id=project_id)
            if project:
                project_map[project.public_id] = project
        return project_map

    def _build_download_info(
        self,
        worker: Worker,
        project_id: str,
        transfer_info: dict,
    ) -> dict[str, object]:
        if not transfer_info.get("file_hash"):
            raise ValueError("项目传输信息缺少 file_hash")

        return {
            "file_hash": transfer_info.get("file_hash") or "",
            "entry_point": transfer_info.get("entry_point") or "",
            "download_url": build_worker_download_url(worker, project_id),
            "is_compressed": transfer_info.get("is_compressed", True),
            "original_name": transfer_info.get("original_name") or "",
            "transfer_method": transfer_info.get("transfer_method") or "",
        }

    def _is_synced(self, results: dict[str, list], project_id: str) -> bool:
        return project_id in results.get("synced", []) or project_id in results.get(
            "skipped",
            [],
        )

    def _append_failure(self, results: dict[str, list], project_id: str, reason: str) -> None:
        results["failed"].append({"project_id": project_id, "reason": reason})


worker_project_sync_service = WorkerProjectSyncService()
