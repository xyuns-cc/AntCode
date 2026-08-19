from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_core.domain.models.enums import ProjectType
from antcode_web_api.routes.v1 import project_duplicate
from pydantic import ValidationError


def _query_returning(value):
    query = MagicMock()
    query.using_db.return_value = query
    query.first = AsyncMock(return_value=value)
    return query


@pytest.mark.asyncio
async def test_duplicate_code_copies_documentation_and_source_on_same_connection(monkeypatch) -> None:
    connection = object()
    detail = SimpleNamespace(
        language="python",
        entry_point="main.py",
        runtime_config={"pythonpath": ["src"]},
        environment_vars={"TOKEN": "encrypted"},
        documentation="usage",
    )
    code_create = AsyncMock()
    monkeypatch.setattr(project_duplicate.ProjectCode, "filter", MagicMock(return_value=_query_returning(detail)))
    monkeypatch.setattr(project_duplicate.ProjectCode, "create", code_create)
    copy_source = AsyncMock()
    monkeypatch.setattr(project_duplicate.project_source_service, "copy_source", copy_source)

    await project_duplicate._duplicate_project_detail(
        SimpleNamespace(id=10, type=ProjectType.CODE),
        SimpleNamespace(id=20),
        connection=connection,
    )

    assert code_create.await_args.kwargs["documentation"] == "usage"
    assert code_create.await_args.kwargs["using_db"] is connection
    copy_source.assert_awaited_once_with(
        source_project_id=10,
        target_project_id=20,
        connection=connection,
    )


@pytest.mark.asyncio
async def test_missing_source_fails_before_duplicate_transaction_can_commit(monkeypatch) -> None:
    connection = object()
    file_detail = SimpleNamespace(
        language="go",
        entry_point="main.go",
        runtime_config={},
        environment_vars={},
    )
    monkeypatch.setattr(
        project_duplicate.ProjectFile,
        "filter",
        MagicMock(return_value=_query_returning(file_detail)),
    )
    monkeypatch.setattr(project_duplicate.ProjectFile, "create", AsyncMock())
    monkeypatch.setattr(
        project_duplicate.project_source_service,
        "copy_source",
        AsyncMock(side_effect=ValueError("源项目缺少 project_sources 配置")),
    )

    with pytest.raises(ValueError, match="project_sources"):
        await project_duplicate._duplicate_project_detail(
            SimpleNamespace(id=10, type=ProjectType.FILE),
            SimpleNamespace(id=20),
            connection=connection,
        )


@pytest.mark.asyncio
async def test_duplicate_project_row_uses_transaction_connection() -> None:
    source = SimpleNamespace(
        description=None,
        type=ProjectType.FILE,
        status="active",
        tags=[],
        dependencies=[],
        env_location="worker",
        worker_id="worker-1",
        worker_env_name="env",
        python_version="3.12",
        runtime_scope="private",
        runtime_kind="python",
        runtime_locator="env",
        current_runtime_id=1,
        execution_strategy="fixed",
        bound_worker_id=2,
    )
    connection = object()

    with patch.object(project_duplicate.Project, "create", AsyncMock(return_value=SimpleNamespace())) as create:
        await project_duplicate._create_duplicate_project(source, "copy", user_id=7, connection=connection)

    assert create.await_args.kwargs["using_db"] is connection


@pytest.mark.asyncio
async def test_duplicate_response_attaches_creator_after_commit(monkeypatch) -> None:
    created = SimpleNamespace(id=20, type=ProjectType.RULE)
    monkeypatch.setattr(
        "antcode_web_api.routes.v1.project.project_service.get_project_by_id",
        AsyncMock(return_value=SimpleNamespace(id=10, name="source", type=ProjectType.RULE)),
    )
    monkeypatch.setattr(
        "antcode_web_api.routes.v1.project._generate_unique_project_name", AsyncMock(return_value="copy")
    )
    monkeypatch.setattr("antcode_web_api.routes.v1.project.duplicate_project_record", AsyncMock(return_value=created))
    attach_creator = AsyncMock()
    monkeypatch.setattr("antcode_web_api.routes.v1.project.project_service._attach_project_creator", attach_creator)
    monkeypatch.setattr("antcode_web_api.routes.v1.project.create_project_response", lambda _project: SimpleNamespace())
    monkeypatch.setattr("antcode_web_api.routes.v1.project._attach_project_detail_info", AsyncMock())

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr("tortoise.transactions.in_transaction", lambda: Transaction())
    from antcode_web_api.routes.v1 import project as project_routes

    await project_routes.duplicate_project(
        "source",
        project_routes.ProjectDuplicateRequest(name="copy"),
        current_user_id=7,
    )

    attach_creator.assert_awaited_once_with(created)


@pytest.mark.asyncio
async def test_duplicate_name_conflict_is_reported_as_409(monkeypatch) -> None:
    from antcode_web_api.routes.v1 import project as project_routes
    from fastapi import HTTPException, status
    from tortoise.exceptions import IntegrityError

    monkeypatch.setattr(
        project_routes.project_service,
        "get_project_by_id",
        AsyncMock(return_value=SimpleNamespace(id=10, name="source", type=ProjectType.RULE)),
    )
    monkeypatch.setattr(project_routes, "_generate_unique_project_name", AsyncMock(return_value="copy"))
    monkeypatch.setattr(
        project_routes,
        "duplicate_project_record",
        AsyncMock(side_effect=IntegrityError("projects_name_key")),
    )

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr("tortoise.transactions.in_transaction", lambda: Transaction())

    with pytest.raises(HTTPException) as exc_info:
        await project_routes.duplicate_project(
            "source",
            project_routes.ProjectDuplicateRequest(name="copy"),
            current_user_id=7,
        )

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT


def test_duplicate_name_is_bounded_by_project_storage_contract() -> None:
    with pytest.raises(ValidationError):
        from antcode_web_api.routes.v1.project import ProjectDuplicateRequest

        ProjectDuplicateRequest(name="x" * 256)

    with pytest.raises(ValidationError):
        ProjectDuplicateRequest(name="   ")


@pytest.mark.asyncio
async def test_generated_duplicate_suffix_stays_within_project_name_limit(monkeypatch) -> None:
    from antcode_web_api.routes.v1 import project as project_routes

    exists = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(project_routes.Project, "filter", MagicMock(return_value=SimpleNamespace(exists=exists)))

    name = await project_routes._generate_unique_project_name("x" * 255)

    assert name == f"{'x' * 253}-1"


@pytest.mark.asyncio
async def test_generated_duplicate_base_is_bounded_without_collision(monkeypatch) -> None:
    from antcode_web_api.routes.v1 import project as project_routes

    monkeypatch.setattr(
        project_routes.Project,
        "filter",
        MagicMock(return_value=SimpleNamespace(exists=AsyncMock(return_value=False))),
    )

    name = await project_routes._generate_unique_project_name("x" * 255 + "-copy")

    assert name == "x" * 255
