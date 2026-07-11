from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.application.services.workers.source_bundle_dispatch_service import (
    SourceBundleDispatchService,
)


class FakeBundleService:
    async def create_git_source_bundle(self, **kwargs):
        assert kwargs["source_config"]["subdir"] == "spiders/news"
        return SimpleNamespace(
            uri="pgartifact://" + "a" * 64,
            sha256="a" * 64,
            size_bytes=321,
            entry_point=kwargs["entry_point"],
            resolved_revision="b" * 40,
            artifact_id=99,
        )


@pytest.mark.asyncio
async def test_dispatch_info_creates_run_source_snapshot():
    service = SourceBundleDispatchService(FakeBundleService())
    transfer_info = {
        "transfer_method": "source_bundle",
        "source": {
            "repository_id": 7,
            "url": "https://example.com/repo.git",
            "subdir": "spiders/news",
            "include_paths": ["libs/common"],
        },
        "entry_point": "main.py",
    }

    with patch(
        "antcode_core.application.services.workers.source_bundle_dispatch_service.RunSourceSnapshot.update_or_create",
        AsyncMock(),
    ) as create_snapshot:
        info = await service._build_source_bundle_dispatch_info(
            project_public_id="project-public",
            project_internal_id=5,
            run_id="run-1",
            transfer_info=transfer_info,
        )

    assert info["source_bundle_uri"] == "pgartifact://" + "a" * 64
    create_snapshot.assert_awaited_once()
    assert create_snapshot.await_args.kwargs["defaults"]["resolved_commit"] == "b" * 40
    assert create_snapshot.await_args.kwargs["defaults"]["include_paths"] == ["libs/common"]
