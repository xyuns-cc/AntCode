from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from antcode_contracts.execution_language import ExecutionLanguage
from antcode_core.application.services.projects.project_source_service import ProjectSourceService


@pytest.mark.asyncio
async def test_project_source_builds_git_source_config_from_repository():
    service = ProjectSourceService()
    repository = SimpleNamespace(
        id=11,
        url="https://example.com/repo.git",
        default_ref="main",
        credential_id="cred-1",
    )
    source = SimpleNamespace(
        project_id=22,
        repository_id=11,
        ref="release",
        subdir="spiders/news",
        include_paths=["libs/common"],
    )

    with (
        patch.object(service, "get_source", AsyncMock(return_value=source)),
        patch.object(
            service,
            "_get_repository",
            AsyncMock(return_value=repository),
        ),
        patch.object(
            service,
            "_get_execution_binding",
            AsyncMock(return_value=("main.py", ExecutionLanguage.PYTHON)),
        ),
    ):
        result = await service.get_transfer_info(22)

    assert result == {
        "transfer_method": "source_bundle",
        "source": {
            "repository_id": 11,
            "url": "https://example.com/repo.git",
            "ref": "release",
            "branch": "release",
            "subdir": "spiders/news",
            "include_paths": ["libs/common"],
            "credential_id": "cred-1",
        },
        "entry_point": "main.py",
        "language": "python",
    }


@pytest.mark.asyncio
async def test_project_source_rejects_missing_source():
    service = ProjectSourceService()

    with patch.object(service, "get_source", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="project_sources"):
            await service.get_transfer_info(22)


@pytest.mark.asyncio
async def test_project_source_update_scopes_repository_to_owner():
    service = ProjectSourceService()
    repository = SimpleNamespace(id=11, public_id="repo-001", name="repo", url="git@example.com:x/repo.git")
    project = SimpleNamespace(id=22, public_id="proj-001")
    source = SimpleNamespace(
        ref="main",
        subdir="spiders/news",
        include_paths=[],
        resolved_commit=None,
    )
    payload = SimpleNamespace(
        repository_id="repo-001",
        ref="main",
        subdir="spiders/news",
        include_paths=[],
    )

    connection = object()
    transaction = AsyncMock()
    transaction.__aenter__.return_value = connection
    transaction.__aexit__.return_value = None
    project_query = SimpleNamespace(
        using_db=lambda _connection: SimpleNamespace(
            select_for_update=lambda: SimpleNamespace(first=AsyncMock(return_value=project))
        )
    )

    with (
        patch(
            "tortoise.transactions.in_transaction",
            return_value=transaction,
        ),
        patch(
            "antcode_core.application.services.projects.project_source_service.Project.filter",
            return_value=project_query,
        ),
        patch.object(
            service,
            "_get_enabled_repository",
            AsyncMock(return_value=repository),
        ) as get_repository,
        patch.object(
            service,
            "upsert_source",
            AsyncMock(return_value=source),
        ) as upsert_source,
    ):
        await service.update_from_payload(22, payload, owner_user_id=100)

    get_repository.assert_awaited_once_with("repo-001", 100, connection=connection)
    upsert_source.assert_awaited_once_with(
        project_id=22,
        repository_id=11,
        ref="main",
        subdir="spiders/news",
        include_paths=[],
        connection=connection,
    )


@pytest.mark.asyncio
async def test_project_source_update_clears_resolved_commit():
    service = ProjectSourceService()
    source = SimpleNamespace(
        repository_id=10,
        ref="old",
        subdir="old/path",
        include_paths=[],
        resolved_commit="a" * 40,
        save=AsyncMock(),
    )

    with patch(
        "antcode_core.application.services.projects.project_source_service.ProjectSource.get_or_none",
        AsyncMock(return_value=source),
    ):
        updated = await service.upsert_source(
            project_id=22,
            repository_id=11,
            ref="main",
            subdir="spiders/news",
            include_paths=["libs/common"],
        )

    assert updated.resolved_commit is None
    source.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_copy_source_preserves_binding_and_resolved_commit_on_connection():
    service = ProjectSourceService()
    connection = object()
    source = SimpleNamespace(
        repository_id=11,
        ref="release",
        subdir="spiders/news",
        include_paths=["libs/common"],
        resolved_commit="a" * 40,
    )
    source_query = SimpleNamespace(
        using_db=lambda _connection: SimpleNamespace(first=AsyncMock(return_value=source)),
    )
    repository_query = SimpleNamespace(
        using_db=lambda _connection: SimpleNamespace(
            select_for_update=lambda: SimpleNamespace(first=AsyncMock(return_value=SimpleNamespace(id=11)))
        ),
    )

    with (
        patch(
            "antcode_core.application.services.projects.project_source_service.ProjectSource.filter",
            return_value=source_query,
        ),
        patch(
            "antcode_core.application.services.projects.project_source_service.GitRepository.filter",
            return_value=repository_query,
        ),
        patch(
            "antcode_core.application.services.projects.project_source_service.ProjectSource.create",
            AsyncMock(return_value=SimpleNamespace()),
        ) as create,
    ):
        await service.copy_source(
            source_project_id=22,
            target_project_id=23,
            connection=connection,
        )

    create.assert_awaited_once_with(
        project_id=23,
        repository_id=11,
        ref="release",
        subdir="spiders/news",
        include_paths=["libs/common"],
        resolved_commit="a" * 40,
        using_db=connection,
    )
