"""Atomic project imports from registered Git repositories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from antcode_contracts.execution_language import (
    ExecutionLanguage,
    ExecutionLanguageError,
    language_from_entry_point,
)
from tortoise.transactions import in_transaction

from antcode_core.application.services.projects.runtime_rollback import (
    RuntimeReservation,
    RuntimeRollback,
)
from antcode_core.application.services.projects.source_bundle_paths import (
    normalize_source_subdir,
    string_list,
)
from antcode_core.application.services.runtime.runtime_control_service import (
    RuntimeControlService,
    runtime_control_service,
)
from antcode_core.application.services.users.user_service import UserService, user_service
from antcode_core.domain.models import GitRepository, Project, ProjectCode, ProjectSource, Worker
from antcode_core.domain.models.enums import ExecutionStrategy, ProjectType, RuntimeKind, RuntimeScope


@dataclass(frozen=True)
class ImportContext:
    connection: Any
    user_id: int
    created_by: str


class RepositoryProjectImporter:
    """Creates one import batch atomically and compensates Worker state."""

    def __init__(self, runtime_service: RuntimeControlService, users: UserService) -> None:
        self._runtime_service = runtime_service
        self._users = users

    async def import_projects(self, user_id: int, items: list[Any]) -> list[str]:
        self._validate_import_items(items)
        created_by = await self._created_by(user_id)
        rollback = RuntimeRollback(self._runtime_service)
        try:
            async with in_transaction("default") as connection:
                context = ImportContext(connection, user_id, created_by)
                return [await self._import_item(context, item, rollback) for item in items]
        except BaseException as primary:
            await rollback.compensate(primary)
            raise

    async def _import_item(self, context: ImportContext, item: Any, rollback: RuntimeRollback) -> str:
        repository = await self._get_repository(context, item.repository_id)
        worker_id = await self._resolve_worker_id(context, item.bound_worker_id)
        project = await self._create_project(context, item, worker_id)
        await self._create_project_code(context, item, project.id)
        await self._create_source(
            context,
            item,
            project_id=project.id,
            repository_id=repository.id,
        )
        reservation = self._runtime_reservation(project, item)
        rollback.register(reservation)
        await self._create_runtime(context, item, reservation)
        project.env_location = "worker"
        project.worker_env_name = reservation.env_name
        await project.save(
            using_db=context.connection,
            update_fields=["env_location", "worker_env_name"],
        )
        return str(project.public_id)

    async def _get_repository(self, context: ImportContext, public_id: str) -> GitRepository:
        repository = await (
            GitRepository.filter(public_id=public_id, enabled=True, owner_user_id=context.user_id)
            .using_db(context.connection)
            .first()
        )
        if repository is None:
            raise ValueError("Git 仓库不存在或不可用")
        return repository

    @staticmethod
    async def _resolve_worker_id(context: ImportContext, public_id: str) -> int:
        worker = await Worker.filter(public_id=public_id).using_db(context.connection).first()
        if worker is None:
            raise ValueError("绑定 Worker 不存在")
        return int(worker.id)

    @staticmethod
    async def _create_project(context: ImportContext, item: Any, worker_id: int) -> Project:
        return await Project.create(
            name=item.name,
            description=item.description,
            type=ProjectType.CODE,
            tags=item.tags or [],
            dependencies=item.dependencies,
            user_id=context.user_id,
            updated_by=context.user_id,
            worker_id=item.worker_id,
            python_version=item.python_version,
            runtime_scope=RuntimeScope(item.runtime_scope),
            runtime_kind=RuntimeKind(item.runtime_kind),
            execution_strategy=ExecutionStrategy(item.execution_strategy),
            bound_worker_id=worker_id,
            using_db=context.connection,
        )

    @staticmethod
    async def _create_project_code(context: ImportContext, item: Any, project_id: int) -> None:
        # 写死 python 是有依据的，不是默认值：_validate_import_items 已确认入口
        # 不是别的语言，且这条路只创建 python 运行时。
        await ProjectCode.create(
            project_id=project_id,
            language=ExecutionLanguage.PYTHON.value,
            entry_point=item.entry_point,
            runtime_config=item.runtime_config,
            using_db=context.connection,
        )

    @staticmethod
    async def _create_source(
        context: ImportContext,
        item: Any,
        *,
        project_id: int,
        repository_id: int,
    ) -> None:
        await ProjectSource.create(
            project_id=project_id,
            repository_id=repository_id,
            ref=(item.ref or "main").strip(),
            subdir=normalize_source_subdir(item.subdir),
            include_paths=string_list(item.include_paths or []),
            using_db=context.connection,
        )

    async def _create_runtime(
        self,
        context: ImportContext,
        item: Any,
        reservation: RuntimeReservation,
    ) -> None:
        result = await self._runtime_service.create_env(
            worker_id=reservation.worker_id,
            env_name=reservation.env_name,
            python_version=item.python_version,
            packages=item.dependencies or [],
            created_by=context.created_by,
            owner_user_id=str(context.user_id),
        )
        if not result.get("success"):
            error = str(result.get("error") or "未知错误")
            raise ValueError(f"创建运行时失败: {error}")

    @staticmethod
    def _runtime_reservation(project: Project, item: Any) -> RuntimeReservation:
        version = item.python_version.replace(".", "")
        return RuntimeReservation(item.worker_id, f"private-{project.public_id}-py{version}")

    async def _created_by(self, user_id: int) -> str:
        user = await self._users.get_user_by_id(user_id)
        return user.username if user else str(user_id)

    @staticmethod
    def _require_python_entry_point(entry_point: Any) -> None:
        """入口后缀必须是 Python（或无后缀）：这条路只会建 python 运行时。

        不拦住的话，导入 ``main.go`` 会存下 language=python 的自相矛盾详情行，
        派发时才被执行语言契约拒掉，错误离用户操作太远。
        """
        try:
            derived = language_from_entry_point(entry_point or "")
        except ExecutionLanguageError as exc:
            raise ValueError(f"仓库导入的入口文件不受支持: {exc}") from exc
        if derived is not None and derived is not ExecutionLanguage.PYTHON:
            raise ValueError(f"仓库导入仅支持 Python 入口，收到 {derived.value} 入口 {entry_point!r}")

    @staticmethod
    def _validate_import_items(items: list[Any]) -> None:
        for item in items:
            if not getattr(item, "worker_id", None) or not getattr(item, "python_version", None):
                raise ValueError("仓库导入必须指定 Worker 和 Python 版本")
            RepositoryProjectImporter._require_python_entry_point(getattr(item, "entry_point", ""))
            if getattr(item, "runtime_scope", None) != RuntimeScope.PRIVATE:
                raise ValueError("仓库导入仅支持新建私有运行时")
            if ExecutionStrategy(item.execution_strategy) != ExecutionStrategy.FIXED_WORKER:
                raise ValueError("仓库导入项目必须固定到运行时所在 Worker")
            if item.worker_id != getattr(item, "bound_worker_id", None):
                raise ValueError("fixed 策略的运行时 Worker 与绑定 Worker 必须一致")


repository_project_importer = RepositoryProjectImporter(runtime_control_service, user_service)

__all__ = ["RepositoryProjectImporter", "RuntimeReservation", "RuntimeRollback", "repository_project_importer"]
