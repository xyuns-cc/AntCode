"""Source bundle dispatch preparation for workers."""

from __future__ import annotations

from loguru import logger

from antcode_core.application.services.projects.source_bundle_service import (
    SourceBundleService,
    source_bundle_service,
)
from antcode_core.domain.models import Project, RunSourceSnapshot, Worker


class SourceBundleDispatchService:
    """Builds immutable source-bundle dispatch info for workers."""

    def __init__(self, bundle_service: SourceBundleService | None = None) -> None:
        self._bundle_service = bundle_service or source_bundle_service

    async def build_dispatch_for_worker_with_info(
        self,
        worker: Worker,
        project_ids: list[str],
        run_ids_by_project: dict[str, str] | None = None,
    ) -> tuple[dict[str, list], dict[str, dict]]:
        del worker
        from antcode_core.application.services.projects.project_sync_service import (
            project_sync_service,
        )

        results: dict[str, list] = {"synced": [], "skipped": [], "failed": []}
        dispatch_info: dict[str, dict] = {}
        project_map = await self._load_project_map(project_ids)

        for project_id in project_ids:
            project = project_map.get(project_id)
            if not project:
                self._append_failure(results, project_id, "项目不存在")
                continue
            try:
                transfer_info = await project_sync_service.get_project_transfer_info(
                    project.id,
                    project=project,
                )
                dispatch_info[project_id] = await self._build_source_bundle_dispatch_info(
                    project_public_id=project.public_id,
                    project_internal_id=project.id,
                    run_id=(run_ids_by_project or {}).get(project.public_id, ""),
                    transfer_info=transfer_info,
                )
                results["synced"].append(project_id)
            except Exception as exc:
                logger.error("构建 source bundle 失败 project={} err={}", project_id, exc)
                self._append_failure(results, project_id, str(exc))

        return results, dispatch_info

    async def _load_project_map(self, project_ids: list[str]) -> dict[str, Project]:
        project_map: dict[str, Project] = {}
        for project_id in set(project_ids):
            project = await Project.get_or_none(public_id=project_id)
            if project:
                project_map[project.public_id] = project
        return project_map

    async def _build_source_bundle_dispatch_info(
        self,
        project_public_id: str,
        project_internal_id: int,
        run_id: str,
        transfer_info: dict,
    ) -> dict[str, object]:
        if transfer_info.get("transfer_method") != "source_bundle":
            raise ValueError("项目传输方式必须是 source_bundle")
        source = transfer_info.get("source")
        if not isinstance(source, dict):
            raise ValueError("source_bundle 缺少 Git 来源配置")
        bundle = await self._bundle_service.create_git_source_bundle(
            project_public_id=project_public_id,
            source_config=source,
            entry_point=str(transfer_info.get("entry_point") or ""),
        )
        await self._record_run_source_snapshot(
            run_id=run_id,
            project_id=project_internal_id,
            source=source,
            transfer_info=transfer_info,
            bundle=bundle,
        )
        return {
            "transfer_method": "source_bundle",
            "source_bundle_uri": bundle.uri,
            "source_bundle_sha256": bundle.sha256,
            "source_bundle_size": bundle.size_bytes,
            "source_subdir": str(source.get("subdir") or ""),
            "entry_point": bundle.entry_point,
            "resolved_revision": bundle.resolved_revision,
        }

    async def _record_run_source_snapshot(
        self,
        *,
        run_id: str,
        project_id: int,
        source: dict,
        transfer_info: dict,
        bundle,
    ) -> None:
        if not run_id:
            raise ValueError("run_id 不能为空，无法记录源码快照")
        repository_id = source.get("repository_id")
        if repository_id is None:
            raise ValueError("source_bundle 缺少 repository_id")
        await RunSourceSnapshot.update_or_create(
            run_id=run_id,
            project_id=project_id,
            defaults={
                "repository_id": int(repository_id),
                "artifact_id": int(bundle.artifact_id),
                "artifact_sha256": bundle.sha256,
                "resolved_commit": bundle.resolved_revision,
                "subdir": str(source.get("subdir") or ""),
                "entry_point": str(transfer_info.get("entry_point") or ""),
                "include_paths": source.get("include_paths") or [],
            },
        )

    def _append_failure(self, results: dict[str, list], project_id: str, reason: str) -> None:
        results["failed"].append({"project_id": project_id, "reason": reason})


source_bundle_dispatch_service = SourceBundleDispatchService()
