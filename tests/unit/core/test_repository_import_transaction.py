from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from antcode_core.application.services.projects import repository_import_service as import_module
from antcode_core.application.services.projects.repository_import_service import RepositoryProjectImporter
from antcode_core.domain.models.enums import ExecutionStrategy, RuntimeKind, RuntimeScope


class _Transaction:
    def __init__(self, *, commit_error: BaseException | None = None) -> None:
        self.connection = object()
        self.commit_error = commit_error
        self.exit_exception: type[BaseException] | None = None

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        self.exit_exception = exc_type
        if exc_type is None and self.commit_error is not None:
            raise self.commit_error
        return False


def _item(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        repository_id="repo-1",
        name=name,
        description=None,
        tags=[],
        dependencies=[],
        runtime_scope=RuntimeScope.PRIVATE,
        runtime_kind=RuntimeKind.PYTHON,
        python_version="3.11",
        worker_id="worker-1",
        bound_worker_id="worker-1",
        execution_strategy=ExecutionStrategy.FIXED_WORKER,
        entry_point="main.py",
        runtime_config={},
        ref="main",
        subdir=name,
        include_paths=[],
    )


def _project(project_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=project_id,
        public_id=f"project-{project_id}",
        save=AsyncMock(),
    )


def _service(runtime_service) -> RepositoryProjectImporter:
    users = SimpleNamespace(
        get_user_by_id=AsyncMock(return_value=SimpleNamespace(username="alice")),
    )
    return RepositoryProjectImporter(runtime_service, users)


def _patch_database(monkeypatch, service, *, transaction, projects) -> None:
    monkeypatch.setattr(import_module, "in_transaction", lambda _name: transaction)
    monkeypatch.setattr(service, "_get_repository", AsyncMock(return_value=SimpleNamespace(id=51)))
    monkeypatch.setattr(service, "_resolve_worker_id", AsyncMock(return_value=61))
    monkeypatch.setattr(import_module.Project, "create", AsyncMock(side_effect=projects))
    monkeypatch.setattr(import_module.ProjectCode, "create", AsyncMock())
    monkeypatch.setattr(import_module.ProjectSource, "create", AsyncMock())


@pytest.mark.asyncio
async def test_import_runtime_failure_rolls_back_entire_batch_and_worker_envs(monkeypatch) -> None:
    transaction = _Transaction()
    runtime = SimpleNamespace(
        create_env=AsyncMock(
            side_effect=[
                {"success": True},
                {"success": False, "error": "python install failed"},
            ]
        ),
        delete_env=AsyncMock(return_value={"success": True}),
    )
    service = _service(runtime)
    projects = [_project(1), _project(2)]
    _patch_database(monkeypatch, service, transaction=transaction, projects=projects)

    with pytest.raises(ValueError, match="python install failed"):
        await service.import_projects(7, [_item("first"), _item("second")])

    assert transaction.exit_exception is ValueError
    assert runtime.delete_env.await_args_list == [
        call("worker-1", "private-project-2-py311"),
        call("worker-1", "private-project-1-py311"),
    ]
    for project in projects[:1]:
        project.save.assert_awaited_once_with(
            using_db=transaction.connection,
            update_fields=["env_location", "worker_env_name"],
        )
    assert import_module.ProjectCode.create.await_args_list[0].kwargs["using_db"] is transaction.connection
    assert import_module.ProjectSource.create.await_args_list[0].kwargs["using_db"] is transaction.connection
    assert import_module.Project.create.await_args_list[0].kwargs["using_db"] is transaction.connection


@pytest.mark.asyncio
async def test_import_commit_failure_compensates_created_runtime(monkeypatch) -> None:
    commit_error = RuntimeError("commit failed")
    transaction = _Transaction(commit_error=commit_error)
    runtime = SimpleNamespace(
        create_env=AsyncMock(return_value={"success": True}),
        delete_env=AsyncMock(return_value={"success": True}),
    )
    service = _service(runtime)
    _patch_database(monkeypatch, service, transaction=transaction, projects=[_project(1)])

    with pytest.raises(RuntimeError, match="commit failed"):
        await service.import_projects(7, [_item("first")])

    runtime.delete_env.assert_awaited_once_with("worker-1", "private-project-1-py311")


@pytest.mark.asyncio
async def test_import_exposes_primary_and_compensation_failures(monkeypatch) -> None:
    transaction = _Transaction()
    runtime = SimpleNamespace(
        create_env=AsyncMock(return_value={"success": False, "error": "create failed"}),
        delete_env=AsyncMock(return_value={"success": False, "error": "delete failed"}),
    )
    service = _service(runtime)
    _patch_database(monkeypatch, service, transaction=transaction, projects=[_project(1)])

    with pytest.raises(BaseExceptionGroup) as exc_info:
        await service.import_projects(7, [_item("first")])

    messages = [str(error) for error in exc_info.value.exceptions]
    assert messages == ["创建运行时失败: create failed", "删除运行时 private-project-1-py311 失败: delete failed"]
