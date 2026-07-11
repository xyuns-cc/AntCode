from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.application.services.projects.project_service import ProjectService


@pytest.mark.asyncio
async def test_file_project_detail_keeps_only_runtime_detail():
    service = ProjectService()
    project = SimpleNamespace(id=1, public_id="proj-001", user_id=100)
    request = SimpleNamespace(
        repository_id="repo-001",
        ref="main",
        subdir="spiders/news",
        include_paths=[],
        entry_point="main.py",
        runtime_config=None,
        environment_vars={"ENV": "prod"},
    )
    repository = SimpleNamespace(
        id=11,
        public_id="repo-001",
        name="crawler-repo",
        url="https://example.com/org/repo.git",
        default_ref="main",
    )
    source = SimpleNamespace(
        repository_id=11,
        ref="main",
        subdir="spiders/news",
        include_paths=[],
        resolved_commit=None,
    )

    with (
        patch.object(
            service,
            "_bind_project_source",
            AsyncMock(return_value=(repository, source)),
        ),
        patch(
            "antcode_core.application.services.projects.project_service.ProjectFile.create",
            AsyncMock(),
        ) as create_file,
    ):
        await service._create_file_project_detail(project, request, None)

    payload = create_file.await_args.kwargs
    assert payload["project_id"] == project.id
    assert "storage_type" not in payload
    assert "file_path" not in payload
    assert "file_hash" not in payload
    assert "original_file_path" not in payload
    assert "source_url" not in payload
    assert "source_subdir" not in payload
    assert "source_name" not in payload
    assert "source_revision" not in payload
    assert payload["entry_point"] == "main.py"
    assert payload["runtime_config"] == {}
