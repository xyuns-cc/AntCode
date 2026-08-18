from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.projects import project_service as project_module
from antcode_core.application.services.projects.project_service import ProjectService
from antcode_core.domain.models.enums import ProjectType, RuntimeKind, RuntimeScope
from fastapi import HTTPException


class _Transaction:
    def __init__(self, commit_error: BaseException | None = None) -> None:
        self.connection = object()
        self.commit_error = commit_error

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, _exc, _traceback):
        if exc_type is None and self.commit_error is not None:
            raise self.commit_error
        return False


def _request(*, use_existing: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        name="file-project",
        description=None,
        type=ProjectType.FILE,
        tags=[],
        dependencies=[],
        repository_id="missing-repository",
        ref="main",
        subdir="src",
        include_paths=[],
        entry_point="main.py",
        runtime_config={},
        environment_vars={},
        runtime_scope=RuntimeScope.PRIVATE,
        runtime_kind=RuntimeKind.PYTHON,
        worker_id="worker-1",
        use_existing_env=use_existing,
        existing_env_name="private-existing" if use_existing else None,
        env_name=None,
        python_version=None if use_existing else "3.11",
        shared_runtime_key=None,
    )


def _project() -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        public_id="project-7",
        user_id=10,
        name="file-project",
        save=AsyncMock(),
    )


def _patch_transaction(monkeypatch, transaction: _Transaction, project) -> None:
    monkeypatch.setattr("tortoise.transactions.in_transaction", lambda: transaction)
    monkeypatch.setattr(project_module.Project, "create", AsyncMock(return_value=project))


@pytest.mark.asyncio
async def test_invalid_repository_is_rejected_before_runtime_creation(monkeypatch) -> None:
    runtime = SimpleNamespace(create_env=AsyncMock(), delete_env=AsyncMock())
    service = ProjectService(runtime)
    transaction = _Transaction()
    _patch_transaction(monkeypatch, transaction, _project())
    monkeypatch.setattr(service, "_attach_project_creator", AsyncMock())
    get_repository = AsyncMock(side_effect=ValueError("Git 仓库不存在或不可用"))
    monkeypatch.setattr(
        project_module.project_source_service,
        "_get_enabled_repository",
        get_repository,
    )

    with pytest.raises(HTTPException, match="Git 仓库不存在或不可用"):
        await service.create_project(_request(), 10)

    runtime.create_env.assert_not_awaited()
    runtime.delete_env.assert_not_awaited()
    get_repository.assert_awaited_once_with(
        "missing-repository",
        10,
        connection=transaction.connection,
    )


@pytest.mark.asyncio
async def test_source_binding_failure_is_rejected_before_runtime_creation(monkeypatch) -> None:
    runtime = SimpleNamespace(create_env=AsyncMock(), delete_env=AsyncMock())
    service = ProjectService(runtime)
    transaction = _Transaction()
    _patch_transaction(monkeypatch, transaction, _project())
    monkeypatch.setattr(service, "_attach_project_creator", AsyncMock())
    monkeypatch.setattr(
        project_module.project_source_service,
        "_get_enabled_repository",
        AsyncMock(return_value=SimpleNamespace(id=11, default_ref="main")),
    )
    monkeypatch.setattr(
        project_module.project_source_service,
        "upsert_source",
        AsyncMock(side_effect=ValueError("Git 源绑定失败")),
    )

    with pytest.raises(HTTPException, match="Git 源绑定失败"):
        await service.create_project(_request(), 10)

    runtime.create_env.assert_not_awaited()
    runtime.delete_env.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_binding_failure_compensates_created_runtime(monkeypatch) -> None:
    runtime = SimpleNamespace(
        create_env=AsyncMock(return_value={"success": True}),
        delete_env=AsyncMock(return_value={"success": True}),
    )
    service = ProjectService(runtime)
    project = _project()
    project.save.side_effect = RuntimeError("runtime binding failed")
    _patch_transaction(monkeypatch, _Transaction(), project)
    monkeypatch.setattr(service, "_create_file_project_detail", AsyncMock())
    monkeypatch.setattr(service, "_attach_project_creator", AsyncMock())
    monkeypatch.setattr(
        service,
        "_authorize_worker",
        AsyncMock(return_value=(SimpleNamespace(id=1, name="worker", public_id="worker-1"), "alice", True)),
    )

    with pytest.raises(HTTPException, match="runtime binding failed"):
        await service.create_project(_request(), 10)

    runtime.delete_env.assert_awaited_once_with("worker-1", "project-project-7-py311")


@pytest.mark.asyncio
async def test_rejected_runtime_creation_is_not_compensated(monkeypatch) -> None:
    runtime = SimpleNamespace(
        create_env=AsyncMock(return_value={"success": False, "error": "already exists"}),
        delete_env=AsyncMock(),
    )
    service = ProjectService(runtime)
    project = _project()
    _patch_transaction(monkeypatch, _Transaction(), project)
    monkeypatch.setattr(service, "_create_file_project_detail", AsyncMock())
    monkeypatch.setattr(service, "_attach_project_creator", AsyncMock())
    monkeypatch.setattr(
        service,
        "_authorize_worker",
        AsyncMock(return_value=(SimpleNamespace(id=1, name="worker", public_id="worker-1"), "alice", True)),
    )

    with pytest.raises(HTTPException, match="already exists"):
        await service.create_project(_request(), 10)

    runtime.delete_env.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit_and_runtime_compensation_failures_are_both_exposed(monkeypatch) -> None:
    runtime = SimpleNamespace(
        create_env=AsyncMock(return_value={"success": True}),
        delete_env=AsyncMock(return_value={"success": False, "error": "delete failed"}),
    )
    service = ProjectService(runtime)
    project = _project()
    _patch_transaction(monkeypatch, _Transaction(RuntimeError("commit failed")), project)
    monkeypatch.setattr(service, "_create_file_project_detail", AsyncMock())
    monkeypatch.setattr(service, "_attach_project_creator", AsyncMock())
    monkeypatch.setattr(
        service,
        "_authorize_worker",
        AsyncMock(return_value=(SimpleNamespace(id=1, name="worker", public_id="worker-1"), "alice", True)),
    )

    with pytest.raises(BaseExceptionGroup) as exc_info:
        await service.create_project(_request(), 10)

    assert [str(error) for error in exc_info.value.exceptions] == [
        "commit failed",
        "删除运行时 project-project-7-py311 失败: delete failed",
    ]
    runtime.delete_env.assert_awaited_once_with("worker-1", "project-project-7-py311")


@pytest.mark.asyncio
async def test_existing_runtime_is_never_deleted_when_commit_fails(monkeypatch) -> None:
    runtime = SimpleNamespace(
        get_env=AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "scope": RuntimeScope.PRIVATE.value,
                    "owner_user_id": "10",
                    "python_version": "3.11",
                },
            }
        ),
        create_env=AsyncMock(),
        delete_env=AsyncMock(),
    )
    service = ProjectService(runtime)
    project = _project()
    _patch_transaction(monkeypatch, _Transaction(RuntimeError("commit failed")), project)
    monkeypatch.setattr(service, "_create_file_project_detail", AsyncMock())
    monkeypatch.setattr(service, "_attach_project_creator", AsyncMock())
    monkeypatch.setattr(
        service,
        "_authorize_worker",
        AsyncMock(return_value=(SimpleNamespace(id=1, name="worker", public_id="worker-1"), "alice", False)),
    )

    with pytest.raises(HTTPException, match="commit failed"):
        await service.create_project(_request(use_existing=True), 10)

    runtime.create_env.assert_not_awaited()
    runtime.delete_env.assert_not_awaited()
