from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.projects.project_source_service import ProjectSourceService
from antcode_core.application.services.projects.repository_import_service import (
    ImportContext,
    RepositoryProjectImporter,
    RuntimeReservation,
)
from antcode_core.application.services.scheduler.execution_resolver import ExecutionResolver
from antcode_core.common.exceptions import WorkerUnavailableError
from antcode_core.domain.models.enums import ExecutionStrategy
from antcode_core.domain.schemas.repository import ImportProjectsPayload, ProjectImportItem
from antcode_web_api.routes.v1 import repositories
from fastapi import HTTPException, Request, status
from pydantic import ValidationError

OPERATOR = SimpleNamespace(user_id=7, username="ops")
HTTP_REQUEST = Request({"type": "http", "client": ("127.0.0.1", 1234)})


def _payload(*, dependencies: list[str] | None = None) -> ImportProjectsPayload:
    return ImportProjectsPayload(
        projects=[
            ProjectImportItem(
                repository_id="repo-1",
                name="imported-project",
                python_version="3.11",
                runtime_scope="private",
                worker_id="worker-1",
                bound_worker_id="worker-1",
                execution_strategy=ExecutionStrategy.FIXED_WORKER,
                dependencies=dependencies,
            )
        ]
    )


@pytest.mark.asyncio
async def test_regular_user_cannot_import_with_unauthorized_worker(monkeypatch) -> None:
    denied = HTTPException(status_code=403, detail="无 Worker 访问权限")
    monkeypatch.setattr(repositories, "ensure_worker_access", AsyncMock(side_effect=denied))
    import_projects = AsyncMock()
    monkeypatch.setattr(repositories.project_source_service, "import_projects", import_projects)

    with pytest.raises(HTTPException) as exc_info:
        await repositories.import_projects_from_repository(
            _payload(), current_user_id=7, current_user=OPERATOR, http_request=HTTP_REQUEST
        )

    assert exc_info.value.status_code == 403
    import_projects.assert_not_awaited()


@pytest.mark.asyncio
async def test_regular_user_can_import_with_authorized_worker(audit_table, monkeypatch) -> None:
    authorize = AsyncMock(return_value=SimpleNamespace(public_id="worker-1"))
    monkeypatch.setattr(repositories, "ensure_worker_access", authorize)
    import_projects = AsyncMock(return_value=["project-1"])
    monkeypatch.setattr(repositories.project_source_service, "import_projects", import_projects)

    response = await repositories.import_projects_from_repository(
        _payload(), current_user_id=7, current_user=OPERATOR, http_request=HTTP_REQUEST
    )

    authorize.assert_awaited_once_with("worker-1", 7)
    import_projects.assert_awaited_once()
    assert response.code == status.HTTP_201_CREATED
    assert response.data.created == ["project-1"]


@pytest.mark.asyncio
async def test_admin_can_import_project_with_runtime_dependencies(audit_table, monkeypatch) -> None:
    authorize = AsyncMock(return_value=SimpleNamespace(public_id="worker-1"))
    monkeypatch.setattr(repositories, "ensure_worker_admin_access", authorize)
    import_projects = AsyncMock(return_value=["project-1"])
    monkeypatch.setattr(repositories.project_source_service, "import_projects", import_projects)

    response = await repositories.import_projects_from_repository(
        _payload(dependencies=["requests==2.32.0"]),
        current_user_id=1,
        current_user=OPERATOR,
        http_request=HTTP_REQUEST,
    )

    authorize.assert_awaited_once_with("worker-1", 1)
    assert response.data.created == ["project-1"]


def test_fixed_import_requires_matching_runtime_and_bound_worker() -> None:
    with pytest.raises(ValidationError, match="运行时 Worker 与绑定 Worker 必须一致"):
        ProjectImportItem(
            repository_id="repo-1",
            name="imported-project",
            python_version="3.11",
            runtime_scope="private",
            worker_id="worker-1",
            bound_worker_id="worker-2",
            execution_strategy=ExecutionStrategy.FIXED_WORKER,
        )


def test_import_schema_requires_worker_and_python_version() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ProjectImportItem(
            repository_id="repo-1",
            name="imported-project",
            bound_worker_id="worker-1",
        )

    missing_fields = {tuple(error["loc"]) for error in exc_info.value.errors()}
    assert ("python_version",) in missing_fields
    assert ("worker_id",) in missing_fields


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"runtime_scope": "shared"}, "仅支持新建私有运行时"),
        ({"execution_strategy": "auto"}, "必须固定到运行时所在 Worker"),
    ],
)
def test_import_schema_rejects_non_executable_runtime_contract(override, message) -> None:
    values = {
        "repository_id": "repo-1",
        "name": "imported-project",
        "python_version": "3.11",
        "worker_id": "worker-1",
        "bound_worker_id": "worker-1",
    }

    with pytest.raises(ValidationError, match=message):
        ProjectImportItem(**values, **override)


@pytest.mark.asyncio
async def test_service_rejects_fixed_mismatch_before_mutation(monkeypatch) -> None:
    service = ProjectSourceService()
    get_repository = AsyncMock()
    monkeypatch.setattr(service, "_get_enabled_repository", get_repository)
    item = SimpleNamespace(
        execution_strategy="fixed",
        runtime_scope="private",
        python_version="3.11",
        worker_id="worker-1",
        bound_worker_id="worker-2",
    )

    with pytest.raises(ValueError, match="运行时 Worker 与绑定 Worker 必须一致"):
        await service.import_projects(7, [item])

    get_repository.assert_not_awaited()


@pytest.mark.parametrize("missing_field", ["worker_id", "python_version"])
@pytest.mark.asyncio
async def test_service_rejects_missing_runtime_before_mutation(monkeypatch, missing_field) -> None:
    service = ProjectSourceService()
    get_repository = AsyncMock()
    monkeypatch.setattr(service, "_get_enabled_repository", get_repository)
    values = {
        "execution_strategy": "fixed",
        "runtime_scope": "private",
        "python_version": "3.11",
        "worker_id": "worker-1",
        "bound_worker_id": "worker-1",
    }
    values[missing_field] = None

    with pytest.raises(ValueError, match="必须指定 Worker 和 Python 版本"):
        await service.import_projects(7, [SimpleNamespace(**values)])

    get_repository.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_runtime_is_created_on_bound_worker(monkeypatch) -> None:
    runtime_service = SimpleNamespace(create_env=AsyncMock(return_value={"success": True}))
    users = SimpleNamespace(get_user_by_id=AsyncMock())
    service = RepositoryProjectImporter(runtime_service, users)
    item = SimpleNamespace(
        worker_id="worker-1",
        python_version="3.11",
        dependencies=[],
    )

    await service._create_runtime(
        ImportContext(connection=object(), user_id=7, created_by="alice"),
        item,
        RuntimeReservation(worker_id="worker-1", env_name="private-project-1-py311"),
    )

    runtime_service.create_env.assert_awaited_once_with(
        worker_id="worker-1",
        env_name="private-project-1-py311",
        python_version="3.11",
        packages=[],
        created_by="alice",
        owner_user_id="7",
    )


@pytest.mark.asyncio
async def test_fixed_dispatch_rejects_runtime_worker_mismatch(monkeypatch) -> None:
    worker = SimpleNamespace(id=9, public_id="worker-2", name="worker-2", status="online")
    project = SimpleNamespace(bound_worker_id=9, worker_id="worker-1")
    monkeypatch.setattr(
        "antcode_core.application.services.scheduler.execution_resolver.Worker.get_or_none",
        AsyncMock(return_value=worker),
    )

    with pytest.raises(WorkerUnavailableError, match="固定 Worker 与项目运行时 Worker 不一致"):
        await ExecutionResolver()._resolve_fixed_worker(project)
