"""Project source bindings backed by git_repositories/project_sources."""

from __future__ import annotations

from antcode_core.application.services.projects.source_bundle_paths import (
    normalize_relative_path,
    normalize_source_subdir,
    string_list,
)
from antcode_core.domain.models import GitRepository, Project, ProjectCode, ProjectFile, ProjectSource
from antcode_core.domain.models.enums import ExecutionStrategy, ProjectType, RuntimeKind, RuntimeScope


class ProjectSourceService:
    """Reads and writes project source bindings."""

    async def get_source(self, project_id: int) -> ProjectSource | None:
        return await ProjectSource.get_or_none(project_id=project_id)

    async def upsert_source(
        self,
        *,
        project_id: int,
        repository_id: int,
        ref: str,
        subdir: str,
        include_paths: list[str] | None = None,
    ) -> ProjectSource:
        normalized_ref, normalized_subdir, normalized_includes = self._source_values(
            ref,
            subdir,
            include_paths,
        )
        source = await ProjectSource.get_or_none(project_id=project_id)
        if source is None:
            return await ProjectSource.create(
                project_id=project_id,
                repository_id=repository_id,
                ref=normalized_ref,
                subdir=normalized_subdir,
                include_paths=normalized_includes,
            )
        source.repository_id = repository_id
        source.ref = normalized_ref
        source.subdir = normalized_subdir
        source.include_paths = normalized_includes
        source.resolved_commit = None
        await source.save()
        return source

    async def get_transfer_info(self, project_id: int) -> dict[str, object]:
        source = await self.get_source(project_id)
        if source is None:
            raise ValueError("项目缺少 project_sources 配置")
        repository = await self._get_repository(source.repository_id)
        if repository is None:
            raise ValueError("项目关联的 Git 仓库不存在")
        entry_point = await self._get_entry_point(project_id)
        return {
            "transfer_method": "source_bundle",
            "source": self._build_source_config(repository, source),
            "entry_point": entry_point,
        }

    async def get_response(self, project_id: int):
        source = await self.get_source(project_id)
        if source is None:
            raise ValueError("项目缺少 project_sources 配置")
        repository = await GitRepository.get(id=source.repository_id)
        project = await Project.get(id=project_id)
        return self._response(project, repository, source)

    async def update_from_payload(self, project_id: int, payload, owner_user_id: int):
        repository = await self._get_enabled_repository(payload.repository_id, owner_user_id)
        source = await self.upsert_source(
            project_id=project_id,
            repository_id=repository.id,
            ref=payload.ref,
            subdir=payload.subdir,
            include_paths=payload.include_paths,
        )
        project = await Project.get(id=project_id)
        return self._response(project, repository, source)

    async def import_projects(self, user_id: int, items: list) -> list[str]:
        created: list[str] = []
        for item in items:
            repository = await self._get_enabled_repository(item.repository_id, user_id)
            project = await self._create_project_shell(user_id, item)
            await ProjectCode.create(
                project_id=project.id,
                language="python",
                entry_point=item.entry_point,
                runtime_config=item.runtime_config,
            )
            await self.upsert_source(
                project_id=project.id,
                repository_id=repository.id,
                ref=item.ref,
                subdir=item.subdir,
                include_paths=item.include_paths,
            )
            # P10: 走 import 路径的 code 项目也必须在 worker 上准备 venv，
            # 否则 dispatch 时 RulePlugin/CodePlugin 拿不到 python_path
            # (`runtime_spec.python_path 不能为空`) 直接失败。之前 O4 只
            # 接了 project/source 建模，漏了运行时准备，import 出的项目
            # 每次都 exit 1。
            await self._ensure_worker_runtime(user_id, project, item)
            created.append(project.public_id)
        return created

    async def _ensure_worker_runtime(self, user_id: int, project, item) -> None:
        """在 worker 上准备 venv 并把 worker_env_name 记回 project。"""
        from antcode_core.application.services.runtime.runtime_control_service import (
            runtime_control_service,
        )
        from antcode_core.application.services.users.user_service import (
            user_service,
        )

        worker_public_id = getattr(item, "worker_id", None)
        if not worker_public_id:
            return
        python_version = getattr(item, "python_version", None) or "3.11"
        scope = str(getattr(item, "runtime_scope", "private") or "private").lower()
        base = f"private-{project.public_id}-py{python_version.replace('.', '')}"
        env_name = f"shared-py{python_version.replace('.', '')}" if scope == "shared" else base
        dependencies = getattr(item, "dependencies", None) or []
        user = await user_service.get_user_by_id(user_id)
        created_by = user.username if user else str(user_id)
        result = await runtime_control_service.create_env(
            worker_id=worker_public_id,
            env_name=env_name,
            python_version=python_version,
            packages=dependencies,
            created_by=created_by,
        )
        if not result.get("success"):
            # 环境已存在时 uv_manager 返回 success=False；共享环境场景下
            # 复用现有环境是正常路径，只有明确 error 才抛。
            err = str(result.get("error") or "")
            if "已存在" not in err and scope != "shared":
                raise ValueError(f"创建运行时失败: {err or '未知错误'}")

        project.env_location = "worker"
        project.worker_env_name = env_name
        await project.save(update_fields=["env_location", "worker_env_name"])

    async def _get_repository(self, repository_id: int) -> GitRepository | None:
        return await GitRepository.get_or_none(id=repository_id, enabled=True)

    async def _get_enabled_repository(
        self,
        repository_public_id: str,
        owner_user_id: int | None = None,
    ) -> GitRepository:
        query = GitRepository.filter(public_id=repository_public_id, enabled=True)
        if owner_user_id is not None:
            query = query.filter(owner_user_id=owner_user_id)
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

    async def _get_entry_point(self, project_id: int) -> str:
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
        detail = await model.get_or_none(project_id=project_id)
        entry_point = getattr(detail, "entry_point", None)
        if not entry_point:
            raise ValueError("项目缺少入口文件配置")
        return normalize_relative_path(entry_point, field_name="入口文件")

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

    async def _create_project_shell(self, user_id: int, item) -> Project:
        bound_worker_id = await self._resolve_worker_id(item.bound_worker_id)
        return await Project.create(
            name=item.name,
            description=item.description,
            type=ProjectType.CODE,
            tags=item.tags or [],
            dependencies=item.dependencies,
            user_id=user_id,
            updated_by=user_id,
            worker_id=item.worker_id,
            python_version=item.python_version,
            runtime_scope=RuntimeScope(item.runtime_scope),
            runtime_kind=RuntimeKind(item.runtime_kind),
            execution_strategy=ExecutionStrategy(item.execution_strategy),
            bound_worker_id=bound_worker_id,
        )

    async def _resolve_worker_id(self, worker_public_id: str | None) -> int | None:
        if not worker_public_id:
            return None
        from antcode_core.domain.models import Worker

        worker = await Worker.get_or_none(public_id=worker_public_id)
        if worker is None:
            raise ValueError("绑定 Worker 不存在")
        return int(worker.id)

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
