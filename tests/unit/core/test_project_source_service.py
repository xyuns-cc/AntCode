from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
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
        patch.object(service, "_get_entry_point", AsyncMock(return_value="main.py")),
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

    with (
        patch.object(
            service,
            "_get_enabled_repository",
            AsyncMock(return_value=repository),
        ) as get_repository,
        patch.object(
            service,
            "upsert_source",
            AsyncMock(return_value=source),
        ),
        patch(
            "antcode_core.application.services.projects.project_source_service.Project.get",
            AsyncMock(return_value=project),
        ),
    ):
        await service.update_from_payload(22, payload, owner_user_id=100)

    get_repository.assert_awaited_once_with("repo-001", 100)


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
