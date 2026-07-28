"""Root identity, bounded discovery, and descriptor lifecycle regressions."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_worker.domain.models import ArtifactRef
from antcode_worker.executor import artifact_io_posix as artifact_io_module
from antcode_worker.executor.artifact_io import ArtifactSecurityError
from antcode_worker.executor.artifacts import ArtifactCollector, ArtifactCollectorConfig, ArtifactManager


def _store() -> AsyncMock:
    store = AsyncMock()
    store.upload_task_artifact.return_value = SimpleNamespace(
        uri="pgartifact://stored",
        content_hash="stored",
        size_bytes=2,
    )
    return store


@pytest.mark.asyncio
async def test_collect_rejects_symlinked_work_root(tmp_path: Path) -> None:
    real_work_dir = tmp_path / "real-work"
    real_work_dir.mkdir()
    linked_work_dir = tmp_path / "linked-work"
    linked_work_dir.symlink_to(real_work_dir, target_is_directory=True)

    with pytest.raises(ArtifactSecurityError, match="符号链接"):
        await ArtifactCollector().collect(str(linked_work_dir), ["result.txt"])


@pytest.mark.asyncio
async def test_collect_rejects_symlinked_work_root_ancestor(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    (real_parent / "work").mkdir(parents=True)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ArtifactSecurityError, match="符号链接"):
        await ArtifactCollector().collect(str(linked_parent / "work"), ["result.txt"])


@pytest.mark.asyncio
async def test_store_rejects_work_root_replaced_after_collection(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "result.txt").write_text("ok", encoding="utf-8")
    store = _store()
    manager = ArtifactManager(artifact_store=store)
    result = await manager.collect_artifacts(str(work_dir), ["result.txt"], "run-1")
    work_dir.rename(tmp_path / "old-work")
    work_dir.mkdir()
    (work_dir / "result.txt").write_text("ok", encoding="utf-8")

    with pytest.raises(ArtifactSecurityError, match="工作目录在收集后发生变化"):
        await manager.store_artifact(result.artifacts[0], "run-1")

    store.upload_task_artifact.assert_not_awaited()


@pytest.mark.asyncio
async def test_collect_stops_after_bounded_candidate_scan(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    for index in range(4):
        (work_dir / f"result-{index}.txt").write_text("ok", encoding="utf-8")
    collector = ArtifactCollector(ArtifactCollectorConfig(max_candidate_count=3))

    with pytest.raises(ArtifactSecurityError, match="候选数量超过上限"):
        await collector.collect(str(work_dir), ["*"])


@pytest.mark.asyncio
async def test_direct_store_rejects_uncollected_external_path(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    store = _store()
    manager = ArtifactManager(artifact_store=store)

    with pytest.raises(ArtifactSecurityError, match="ArtifactCollector"):
        await manager.store_artifact(
            ArtifactRef(name="outside.txt", local_path=str(outside)),
            "run-1",
        )

    store.upload_task_artifact.assert_not_awaited()


def test_file_descriptor_is_closed_when_fstat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    artifact_path = work_dir / "result.txt"
    artifact_path.write_text("ok", encoding="utf-8")
    root = artifact_io_module.inspect_work_root(work_dir)
    identity = artifact_io_module.inspect_candidate(artifact_path, artifact_path.name)
    assert identity is not None
    captured: dict[str, int] = {}
    original_open_leaf = artifact_io_module._open_leaf
    original_fstat = artifact_io_module.os.fstat

    def capture_leaf(*args, **kwargs):
        fd = original_open_leaf(*args, **kwargs)
        captured["fd"] = fd
        return fd

    def fail_file_fstat(fd: int):
        if fd == captured.get("fd"):
            raise OSError("forced fstat failure")
        return original_fstat(fd)

    monkeypatch.setattr(artifact_io_module, "_open_leaf", capture_leaf)
    monkeypatch.setattr(artifact_io_module.os, "fstat", fail_file_fstat)

    with pytest.raises(OSError, match="forced fstat failure"):
        artifact_io_module.hash_regular_file(
            root.path,
            artifact_path.name,
            expected_root=root.identity,
            expected=identity,
            algorithm="sha256",
            max_bytes=identity.size,
        )

    with pytest.raises(OSError):
        original_fstat(captured["fd"])
