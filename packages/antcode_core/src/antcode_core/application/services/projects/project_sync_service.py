"""Project transfer contract service."""

from __future__ import annotations

from fastapi import HTTPException, status

from antcode_core.domain.models import Project


class ProjectSyncService:
    """Resolves project source information for Master-side bundling."""

    async def get_project_transfer_info(
        self,
        project_id: int | str,
        project: Project | None = None,
    ) -> dict[str, object]:
        resolved_project = project or await Project.get_or_none(id=project_id)
        if not resolved_project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

        project_type = self._project_type_value(resolved_project.type)
        self._ensure_source_bundle_project_type(project_type)
        from antcode_core.application.services.projects.project_source_service import (
            project_source_service,
        )

        try:
            return await project_source_service.get_transfer_info(resolved_project.id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @staticmethod
    def _project_type_value(project_type: object) -> str:
        value = getattr(project_type, "value", project_type)
        return str(value)

    def _ensure_source_bundle_project_type(self, project_type: str) -> None:
        if project_type not in {"code", "file"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="源码包派发只支持 Git 文件或代码项目",
            )


project_sync_service = ProjectSyncService()
