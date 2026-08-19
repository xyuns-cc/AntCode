"""Project source bindings backed by git_repositories/project_sources."""

from __future__ import annotations

from typing import Any

from antcode_contracts.execution_language import (
    ExecutionLanguage,
    ExecutionLanguageError,
    resolve_execution_language,
)

from antcode_core.application.services.projects.source_bundle_paths import (
    normalize_relative_path,
    normalize_source_subdir,
    string_list,
)
from antcode_core.domain.models import GitRepository, Project, ProjectCode, ProjectFile, ProjectSource
from antcode_core.domain.models.enums import ProjectType


class ProjectSourceService:
    """Reads and writes project source bindings."""

    async def get_source(self, project_id: int, connection: Any | None = None) -> ProjectSource | None:
        query = ProjectSource.filter(project_id=project_id)
        if connection is not None:
            query = query.using_db(connection)
        return await query.first()

    async def copy_source(
        self,
        *,
        source_project_id: int,
        target_project_id: int,
        connection: Any,
    ) -> ProjectSource:
        source = await ProjectSource.filter(project_id=source_project_id).using_db(connection).first()
        if source is None:
            raise ValueError("源项目缺少 project_sources 配置")
        repository = (
            await GitRepository.filter(id=source.repository_id).using_db(connection).select_for_update().first()
        )
        if repository is None:
            raise ValueError("源项目关联的 Git 仓库不存在")
        return await ProjectSource.create(
            project_id=target_project_id,
            repository_id=source.repository_id,
            ref=source.ref,
            subdir=source.subdir,
            include_paths=list(source.include_paths or []),
            resolved_commit=source.resolved_commit,
            using_db=connection,
        )

    async def upsert_source(
        self,
        *,
        project_id: int,
        repository_id: int,
        ref: str,
        subdir: str,
        include_paths: list[str] | None = None,
        connection: Any | None = None,
    ) -> ProjectSource:
        normalized_ref, normalized_subdir, normalized_includes = self._source_values(
            ref,
            subdir,
            include_paths,
        )
        if connection is None:
            source = await ProjectSource.get_or_none(project_id=project_id)
        else:
            source = await ProjectSource.filter(project_id=project_id).using_db(connection).first()
        if source is None:
            return await self._create_source(
                project_id=project_id,
                repository_id=repository_id,
                ref=normalized_ref,
                subdir=normalized_subdir,
                include_paths=normalized_includes,
                connection=connection,
            )
        source.repository_id = repository_id
        source.ref = normalized_ref
        source.subdir = normalized_subdir
        source.include_paths = normalized_includes
        source.resolved_commit = None
        if connection is None:
            await source.save()
        else:
            await source.save(using_db=connection)
        return source

    @staticmethod
    async def _create_source(
        *,
        project_id: int,
        repository_id: int,
        ref: str,
        subdir: str,
        include_paths: list[str],
        connection: Any | None,
    ) -> ProjectSource:
        if connection is None:
            return await ProjectSource.create(
                project_id=project_id,
                repository_id=repository_id,
                ref=ref,
                subdir=subdir,
                include_paths=include_paths,
            )
        return await ProjectSource.create(
            project_id=project_id,
            repository_id=repository_id,
            ref=ref,
            subdir=subdir,
            include_paths=include_paths,
            using_db=connection,
        )

    async def get_transfer_info(self, project_id: int) -> dict[str, object]:
        source = await self.get_source(project_id)
        if source is None:
            raise ValueError("项目缺少 project_sources 配置")
        repository = await self._get_repository(source.repository_id)
        if repository is None:
            raise ValueError("项目关联的 Git 仓库不存在")
        entry_point, language = await self._get_execution_binding(project_id)
        return {
            "transfer_method": "source_bundle",
            "source": self._build_source_config(repository, source),
            "entry_point": entry_point,
            "language": language.value,
        }

    async def get_response(self, project_id: int):
        source = await self.get_source(project_id)
        if source is None:
            raise ValueError("项目缺少 project_sources 配置")
        repository = await GitRepository.get(id=source.repository_id)
        project = await Project.get(id=project_id)
        return self._response(project, repository, source)

    async def update_from_payload(self, project_id: int, payload, owner_user_id: int):
        from tortoise.transactions import in_transaction

        async with in_transaction("default") as connection:
            project = await Project.filter(id=project_id).using_db(connection).select_for_update().first()
            if project is None:
                raise ValueError("项目不存在")
            repository = await self._get_enabled_repository(
                payload.repository_id,
                owner_user_id,
                connection=connection,
            )
            source = await self.upsert_source(
                project_id=project_id,
                repository_id=repository.id,
                ref=payload.ref,
                subdir=payload.subdir,
                include_paths=payload.include_paths,
                connection=connection,
            )
        return self._response(project, repository, source)

    async def import_projects(self, user_id: int, items: list) -> list[str]:
        from antcode_core.application.services.projects.repository_import_service import (
            repository_project_importer,
        )

        return await repository_project_importer.import_projects(user_id, items)

    async def _get_repository(self, repository_id: int) -> GitRepository | None:
        return await GitRepository.get_or_none(id=repository_id, enabled=True)

    async def _get_enabled_repository(
        self,
        repository_public_id: str,
        owner_user_id: int | None = None,
        connection: Any | None = None,
    ) -> GitRepository:
        query = GitRepository.filter(public_id=repository_public_id, enabled=True)
        if owner_user_id is not None:
            query = query.filter(owner_user_id=owner_user_id)
        if connection is not None:
            query = query.using_db(connection).select_for_update()
        repository = await query.first()
        if repository is None:
            raise ValueError("Git 仓库不存在或不可用")
        return repository

    def _source_values(
        self,
        ref: str,
        subdir: str,
        include_paths: list[str] | None,
    ) -> tuple[str, str, list[str]]:
        return (
            (ref or "main").strip(),
            normalize_source_subdir(subdir),
            string_list(include_paths or []),
        )

    async def _get_execution_binding(self, project_id: int) -> tuple[str, ExecutionLanguage]:
        """入口文件与执行语言必须同时确定：两者矛盾的项目在派发准备阶段就失败。

        放在这里而不是放到 Worker，是因为这一步跑在 Master 构建 source bundle
        之前——矛盾的项目根本不会产出可派发的任务，用户在 run 的失败原因里直接
        看到该改哪个字段，而不是拿到一份用错解释器的执行日志。
        """
        detail = await self._get_source_detail(project_id)
        entry_point = getattr(detail, "entry_point", None)
        if not entry_point:
            raise ValueError("项目缺少入口文件配置")
        normalized_entry = normalize_relative_path(entry_point, field_name="入口文件")
        try:
            language = resolve_execution_language(getattr(detail, "language", None), normalized_entry)
        except ExecutionLanguageError as exc:
            raise ValueError(str(exc)) from exc
        return normalized_entry, language

    @staticmethod
    async def _get_source_detail(project_id: int) -> ProjectCode | ProjectFile | None:
        project = await Project.get_or_none(id=project_id)
        if project is None:
            raise ValueError("项目不存在")
        model: type[ProjectCode] | type[ProjectFile]
        if project.type == ProjectType.CODE:
            model = ProjectCode
        elif project.type == ProjectType.FILE:
            model = ProjectFile
        else:
            raise ValueError("只有 Git 文件或代码项目支持源码包")
        return await model.get_or_none(project_id=project_id)

    def _build_source_config(self, repository, source) -> dict[str, object]:
        config: dict[str, object] = {
            "repository_id": repository.id,
            "url": repository.url,
            "ref": source.ref,
            "branch": source.ref,
            "subdir": source.subdir,
            "include_paths": source.include_paths or [],
        }
        credential_id = getattr(repository, "credential_id", None)
        if credential_id:
            config["credential_id"] = credential_id
        return config

    def _response(self, project, repository, source):
        from antcode_core.domain.schemas.project_source import ProjectSourceResponse

        return ProjectSourceResponse(
            project_id=project.public_id,
            repository_id=repository.public_id,
            repository_name=repository.name,
            repository_url=repository.url,
            ref=source.ref,
            subdir=source.subdir,
            include_paths=source.include_paths or [],
            resolved_commit=source.resolved_commit,
        )


project_source_service = ProjectSourceService()
