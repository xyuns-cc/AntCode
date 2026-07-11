from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.application.services.projects.project_sync_service import (
    ProjectSyncService,
)
from antcode_core.domain.models.enums import ProjectType


@pytest.mark.asyncio
async def test_file_git_project_resolves_source_bundle_transfer_info():
    service = ProjectSyncService()
    project = SimpleNamespace(id=42, type=ProjectType.FILE)
    transfer_info = {
        "transfer_method": "source_bundle",
        "source": {
            "repository_id": 11,
            "url": "https://example.com/org/repo.git",
            "branch": "main",
            "ref": "main",
            "subdir": "spiders/news",
            "include_paths": [],
        },
        "entry_point": "main.py",
    }

    with patch(
        "antcode_core.application.services.projects.project_source_service.project_source_service.get_transfer_info",
        AsyncMock(return_value=transfer_info),
    ):
        info = await service.get_project_transfer_info(project.id, project=project)

    assert info == transfer_info


@pytest.mark.asyncio
async def test_rule_project_is_rejected_for_source_bundle_dispatch():
    service = ProjectSyncService()
    project = SimpleNamespace(id=42, type=ProjectType.RULE)

    with pytest.raises(Exception, match="Git 文件或代码项目"):
        await service.get_project_transfer_info(project.id, project=project)
