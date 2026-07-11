from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.application.services.workers.source_bundle_dispatch_service import (
    SourceBundleDispatchService,
)


class FakeBundle:
    uri = "pgartifact://bundle-sha"
    sha256 = "bundle-sha"
    size_bytes = 123
    entry_point = "main.py"
    resolved_revision = "a" * 40
    artifact_id = 99


class FakeBundleService:
    def __init__(self):
        self.calls = []

    async def create_git_source_bundle(self, **kwargs):
        self.calls.append(kwargs)
        return FakeBundle()


@pytest.mark.asyncio
async def test_dispatch_service_builds_source_bundle_dispatch_info():
    bundle_service = FakeBundleService()
    service = SourceBundleDispatchService(bundle_service)

    with patch(
        "antcode_core.application.services.workers.source_bundle_dispatch_service.RunSourceSnapshot.update_or_create",
        AsyncMock(),
    ):
        info = await service._build_source_bundle_dispatch_info(
            project_public_id="proj-1",
            project_internal_id=1,
            run_id="run-1",
            transfer_info={
                "transfer_method": "source_bundle",
                "source": {
                    "repository_id": 7,
                    "url": "https://example.com/org/repo.git",
                    "commit": "a" * 40,
                    "subdir": "spiders/news",
                },
                "entry_point": "main.py",
            },
        )

    assert info == {
        "transfer_method": "source_bundle",
        "source_bundle_uri": "pgartifact://bundle-sha",
        "source_bundle_sha256": "bundle-sha",
        "source_bundle_size": 123,
        "source_subdir": "spiders/news",
        "entry_point": "main.py",
        "resolved_revision": "a" * 40,
    }
    assert bundle_service.calls[0]["project_public_id"] == "proj-1"


@pytest.mark.asyncio
async def test_dispatch_service_rejects_legacy_transfer_info():
    service = SourceBundleDispatchService(FakeBundleService())

    with pytest.raises(ValueError, match="source_bundle"):
        await service._build_source_bundle_dispatch_info(
            project_public_id="proj-1",
            project_internal_id=1,
            run_id="run-1",
            transfer_info={"transfer_method": "managed_archive", "download_url": "http://example.com"},
        )


def test_dispatch_service_has_no_legacy_sync_methods():
    assert not hasattr(SourceBundleDispatchService, "sync_single_project")
    assert not hasattr(SourceBundleDispatchService, "sync_project_to_worker")
    assert not hasattr(SourceBundleDispatchService, "sync_projects_to_worker")
    assert not hasattr(SourceBundleDispatchService, "sync_projects_to_worker_with_info")
