from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot
from antcode_core.application.services.workers.source_bundle_dispatch_service import (
    SourceBundleDispatchService,
)
from antcode_core.application.services.workers.worker_dispatcher import WorkerTaskDispatcher


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

    snapshot_path = "antcode_core.application.services.workers.source_bundle_dispatch_service.RunSourceSnapshot"

    # P1-DB-02: 快照事务内先对 artifact 行取 FOR KEY SHARE。
    class _FakeConn:
        async def execute_query(self, sql, params=None):
            assert "FOR KEY SHARE" in sql
            return 1, [{"id": 99}]

    class _FakeTransaction:
        async def __aenter__(self):
            return _FakeConn()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    with (
        patch(f"{snapshot_path}.get_or_none", AsyncMock(return_value=None)),
        patch(f"{snapshot_path}.get_or_create", AsyncMock(return_value=(object(), True))),
        patch(
            "antcode_core.application.services.workers.source_bundle_dispatch_service.in_transaction",
            lambda _alias: _FakeTransaction(),
        ),
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


class _BatchFakeConn:
    async def execute_query(self, sql, params=None):
        assert "FOR KEY SHARE" in sql
        return 1, [{"id": 99}]


class _BatchFakeTransaction:
    async def __aenter__(self):
        return _BatchFakeConn()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def _transfer_info():
    return {
        "transfer_method": "source_bundle",
        "source": {
            "repository_id": 7,
            "url": "https://example.com/org/repo.git",
            "subdir": "new/subdir",
        },
        "entry_point": "main.py",
    }


@pytest.mark.asyncio
async def test_same_project_batch_builds_once_and_records_every_run():
    bundle_service = FakeBundleService()
    service = SourceBundleDispatchService(bundle_service)
    project = SimpleNamespace(id=1, public_id="proj-1")
    snapshots = AsyncMock(side_effect=[(object(), True), (object(), True)])

    with (
        patch.object(service, "_load_project_map", AsyncMock(return_value={"proj-1": project})),
        patch(
            "antcode_core.application.services.projects.project_sync_service."
            "project_sync_service.get_project_transfer_info",
            AsyncMock(return_value=_transfer_info()),
        ),
        patch(
            "antcode_core.application.services.workers.source_bundle_dispatch_service.RunSourceSnapshot.get_or_none",
            AsyncMock(return_value=None),
        ),
        patch(
            "antcode_core.application.services.workers.source_bundle_dispatch_service.RunSourceSnapshot.get_or_create",
            snapshots,
        ),
        patch(
            "antcode_core.application.services.workers.source_bundle_dispatch_service.in_transaction",
            lambda _alias: _BatchFakeTransaction(),
        ),
    ):
        results, info = await service.build_dispatch_for_worker_with_info(
            object(),
            ["proj-1"],
            run_ids_by_project={"proj-1": ["run-1", "run-2"]},
        )

    assert results["failed"] == []
    assert set(info) == {"run-1", "run-2"}
    assert len(bundle_service.calls) == 1
    assert [call.kwargs["run_id"] for call in snapshots.await_args_list] == ["run-1", "run-2"]


@pytest.mark.asyncio
async def test_batch_preserves_existing_snapshot_while_building_missing_run():
    bundle_service = FakeBundleService()
    service = SourceBundleDispatchService(bundle_service)
    existing = SimpleNamespace(
        artifact_sha256="c" * 64,
        resolved_commit="d" * 40,
        subdir="old/subdir",
        entry_point="old.py",
    )
    stored = SimpleNamespace(size_bytes=777)

    async def find_snapshot(*, run_id, project_id):
        assert project_id == 1
        return existing if run_id == "run-old" else None

    with (
        patch(
            "antcode_core.application.services.workers.source_bundle_dispatch_service.RunSourceSnapshot.get_or_none",
            AsyncMock(side_effect=find_snapshot),
        ),
        patch(
            "antcode_core.application.services.workers.source_bundle_dispatch_service.RunSourceSnapshot.get_or_create",
            AsyncMock(return_value=(object(), True)),
        ) as create_snapshot,
        patch(
            "antcode_core.infrastructure.postgres.artifact_store.PostgresArtifactStore.stat_blob",
            AsyncMock(return_value=stored),
        ),
        patch(
            "antcode_core.application.services.workers.source_bundle_dispatch_service.in_transaction",
            lambda _alias: _BatchFakeTransaction(),
        ),
    ):
        info = await service._build_source_bundle_dispatch_infos(
            project_public_id="proj-1",
            project_internal_id=1,
            run_ids=["run-old", "run-new"],
            transfer_info=_transfer_info(),
        )

    assert info["run-old"]["source_bundle_sha256"] == "c" * 64
    assert info["run-old"]["source_subdir"] == "old/subdir"
    assert info["run-new"]["source_bundle_sha256"] == "bundle-sha"
    assert len(bundle_service.calls) == 1
    create_snapshot.assert_awaited_once()
    assert create_snapshot.await_args.kwargs["run_id"] == "run-new"


@pytest.mark.asyncio
async def test_dispatcher_enriches_same_project_tasks_by_run(monkeypatch):
    dispatcher = WorkerTaskDispatcher()
    worker = SimpleNamespace(
        id=7,
        public_id="worker-7",
        name="Worker 7",
        transport_mode="direct",
        capabilities={"task_types": ["code", "rule"]},
    )
    bundle_dispatch = SimpleNamespace(
        build_dispatch_for_worker_with_info=AsyncMock(
            return_value=(
                {"synced": ["proj-1"], "skipped": [], "failed": []},
                {
                    "run-1": {"source_bundle_sha256": "sha-1"},
                    "run-2": {"source_bundle_sha256": "sha-2"},
                },
            )
        )
    )
    published: list[dict] = []

    async def publish(*, tasks, **_kwargs):
        published.extend(tasks)
        return {"success": True, "accepted_count": 3, "rejected_count": 0}

    dispatcher._select_worker = AsyncMock(return_value=worker)
    dispatcher._ensure_worker_connected = AsyncMock(return_value=True)
    dispatcher._bind_task_runs_to_worker = AsyncMock(return_value=2)
    dispatcher._send_batch_to_queue = publish
    monkeypatch.setattr(
        "antcode_core.application.services.workers.worker_dispatcher.require_worker_current_requirements",
        AsyncMock(return_value=LeaseCapabilitySnapshot("lease-7", '{"task_types":["code","rule"]}', 7)),
    )
    dispatch_module = import_module("antcode_core.application.services.workers.source_bundle_dispatch_service")
    monkeypatch.setattr(dispatch_module, "source_bundle_dispatch_service", bundle_dispatch)

    result = await dispatcher.dispatch_batch(
        tasks=[
            {"task_id": "run-1", "project_id": "proj-1", "project_type": "code"},
            {"task_id": "run-2", "project_id": "proj-1", "project_type": "code"},
            {"task_id": "run-rule", "project_id": "proj-1", "project_type": "rule"},
        ]
    )

    assert result.success is True
    call = bundle_dispatch.build_dispatch_for_worker_with_info.await_args
    assert call.kwargs["run_ids_by_project"] == {"proj-1": ["run-1", "run-2"]}
    assert [task["source_bundle_sha256"] for task in published[:2]] == ["sha-1", "sha-2"]
    assert "source_bundle_sha256" not in published[2]
