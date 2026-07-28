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


_TRANSFER_INFO = {
    "transfer_method": "source_bundle",
    "source": {
        "repository_id": 7,
        "url": "https://example.com/repo.git",
        "subdir": "spiders/news",
        "include_paths": ["libs/common"],
    },
    "entry_point": "main.py",
}

_SNAPSHOT_PATH = "antcode_core.application.services.workers.source_bundle_dispatch_service.RunSourceSnapshot"
_SERVICE_MODULE = "antcode_core.application.services.workers.source_bundle_dispatch_service"


class _FakeConn:
    """P1-DB-02: 快照事务内先对 artifact 行取 FOR KEY SHARE 再写快照。"""

    def __init__(self, artifact_rows=None):
        self.queries: list[tuple[str, list]] = []
        self._artifact_rows = [{"id": 99}] if artifact_rows is None else artifact_rows

    async def execute_query(self, sql, params=None):
        self.queries.append((sql, params or []))
        assert "FOR KEY SHARE" in sql
        return len(self._artifact_rows), self._artifact_rows


class _FakeTransaction:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def _patch_transaction(conn):
    return patch(f"{_SERVICE_MODULE}.in_transaction", lambda _alias: _FakeTransaction(conn))


@pytest.mark.asyncio
async def test_dispatch_info_creates_run_source_snapshot():
    service = SourceBundleDispatchService(FakeBundleService())

    snapshot = SimpleNamespace()
    conn = _FakeConn()
    with (
        patch(f"{_SNAPSHOT_PATH}.get_or_none", AsyncMock(return_value=None)),
        patch(f"{_SNAPSHOT_PATH}.get_or_create", AsyncMock(return_value=(snapshot, True))) as create_snapshot,
        _patch_transaction(conn),
    ):
        info = await service._build_source_bundle_dispatch_info(
            project_public_id="project-public",
            project_internal_id=5,
            run_id="run-1",
            transfer_info=_TRANSFER_INFO,
        )

    assert info["source_bundle_uri"] == "pgartifact://" + "a" * 64
    create_snapshot.assert_awaited_once()
    defaults = create_snapshot.await_args.kwargs["defaults"]
    assert defaults["resolved_commit"] == "b" * 40
    assert defaults["include_paths"] == ["libs/common"]
    # P1-DB-02: 与 cleanup FOR UPDATE 互斥的 KEY SHARE 锁在同一事务内先行。
    assert conn.queries and conn.queries[0][1] == [99]
    assert create_snapshot.await_args.kwargs["using_db"] is conn


@pytest.mark.asyncio
async def test_redispatch_reuses_existing_immutable_snapshot():
    """P1-DB-02: 同一 run 重投不得重建 bundle 覆盖快照 —— 按已记录快照派发。"""
    bundle_service = FakeBundleService()
    bundle_service.create_git_source_bundle = AsyncMock(side_effect=AssertionError("已有快照时不得重建 bundle"))
    service = SourceBundleDispatchService(bundle_service)
    existing = SimpleNamespace(
        artifact_sha256="c" * 64,
        resolved_commit="d" * 40,
        subdir="spiders/news",
        entry_point="main.py",
    )
    stored = SimpleNamespace(size_bytes=777)

    with (
        patch(f"{_SNAPSHOT_PATH}.get_or_none", AsyncMock(return_value=existing)),
        patch(
            "antcode_core.infrastructure.postgres.artifact_store.PostgresArtifactStore.stat_blob",
            AsyncMock(return_value=stored),
        ),
    ):
        info = await service._build_source_bundle_dispatch_info(
            project_public_id="project-public",
            project_internal_id=5,
            run_id="run-1",
            transfer_info=_TRANSFER_INFO,
        )

    assert info["source_bundle_uri"] == "pgartifact://" + "c" * 64
    assert info["source_bundle_sha256"] == "c" * 64
    assert info["source_bundle_size"] == 777
    assert info["resolved_revision"] == "d" * 40


@pytest.mark.asyncio
async def test_concurrent_first_writer_wins_and_new_bundle_reference_is_dropped():
    """P1-DB-02: 构建期间他人先写入快照 → first-write-wins，按其快照派发。"""
    service = SourceBundleDispatchService(FakeBundleService())
    winner = SimpleNamespace(
        artifact_sha256="e" * 64,
        resolved_commit="f" * 40,
        subdir="spiders/news",
        entry_point="main.py",
    )
    stored = SimpleNamespace(size_bytes=555)

    with (
        patch(f"{_SNAPSHOT_PATH}.get_or_none", AsyncMock(return_value=None)),
        patch(f"{_SNAPSHOT_PATH}.get_or_create", AsyncMock(return_value=(winner, False))),
        _patch_transaction(_FakeConn()),
        patch(
            "antcode_core.infrastructure.postgres.artifact_store.PostgresArtifactStore.stat_blob",
            AsyncMock(return_value=stored),
        ),
    ):
        info = await service._build_source_bundle_dispatch_info(
            project_public_id="project-public",
            project_internal_id=5,
            run_id="run-1",
            transfer_info=_TRANSFER_INFO,
        )

    assert info["source_bundle_sha256"] == "e" * 64
    assert info["source_bundle_size"] == 555


@pytest.mark.asyncio
async def test_snapshot_fails_closed_when_artifact_cleaned_concurrently():
    """P1-DB-02: KEY SHARE 查不到 artifact 行（cleanup 先到）→ 显式失败。"""
    service = SourceBundleDispatchService(FakeBundleService())

    with (
        patch(f"{_SNAPSHOT_PATH}.get_or_none", AsyncMock(return_value=None)),
        patch(f"{_SNAPSHOT_PATH}.get_or_create", AsyncMock()) as create_snapshot,
        _patch_transaction(_FakeConn(artifact_rows=[])),
        pytest.raises(RuntimeError, match="已被并发清理"),
    ):
        await service._build_source_bundle_dispatch_info(
            project_public_id="project-public",
            project_internal_id=5,
            run_id="run-1",
            transfer_info=_TRANSFER_INFO,
        )

    create_snapshot.assert_not_awaited()
