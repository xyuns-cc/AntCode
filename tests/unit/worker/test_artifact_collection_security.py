"""Security regression tests for Worker artifact collection and storage."""

import os
import socket
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_worker.executor import artifact_collector as collector_module
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
async def test_collect_and_store_nested_regular_file(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    artifact_path = work_dir / "reports" / "result.txt"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("ok", encoding="utf-8")
    store = _store()
    manager = ArtifactManager(artifact_store=store)

    result = await manager.collect_artifacts(str(work_dir), ["**/*"], "run-1")
    stored = await manager.store_artifact(result.artifacts[0], "run-1")

    assert [artifact.name for artifact in result.artifacts] == ["reports/result.txt"]
    assert stored.uri == "pgartifact://stored"
    request = store.upload_task_artifact.await_args.args[0]
    assert request.content == b"ok"
    assert request.media_type == "text/plain"
    assert request.run_id == "run-1"


@pytest.mark.asyncio
async def test_collect_rejects_symlinked_file(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    outside = tmp_path / "worker-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (work_dir / "result.txt").symlink_to(outside)

    with pytest.raises(ArtifactSecurityError, match="常规文件"):
        await ArtifactCollector().collect(str(work_dir), ["result.txt"])


@pytest.mark.asyncio
async def test_collect_rejects_symlinked_parent_directory(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    outside_dir = tmp_path / "secrets"
    work_dir.mkdir()
    outside_dir.mkdir()
    (outside_dir / "token.txt").write_text("secret", encoding="utf-8")
    (work_dir / "linked").symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(ArtifactSecurityError, match="父目录不安全"):
        await ArtifactCollector().collect(str(work_dir), ["linked/token.txt"])


@pytest.mark.asyncio
async def test_collect_rejects_hardlinked_file(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    outside = tmp_path / "worker-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    os.link(outside, work_dir / "result.txt")

    with pytest.raises(ArtifactSecurityError, match="硬链接"):
        await ArtifactCollector().collect(str(work_dir), ["result.txt"])


@pytest.mark.asyncio
async def test_collect_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    os.mkfifo(work_dir / "result.pipe")

    with pytest.raises(ArtifactSecurityError, match="常规文件"):
        await ArtifactCollector().collect(str(work_dir), ["result.pipe"])


@pytest.mark.asyncio
async def test_collect_rejects_unix_socket() -> None:
    socket_temp_dir = "/private/tmp" if Path("/private/tmp").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="ac-artifact-", dir=socket_temp_dir) as directory:
        work_dir = Path(directory)
        socket_path = work_dir / "result.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(socket_path))
        try:
            with pytest.raises(ArtifactSecurityError, match="常规文件"):
                await ArtifactCollector().collect(str(work_dir), ["result.sock"])
        finally:
            server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", ["../worker-secret.txt", "/etc/passwd"])
async def test_collect_rejects_path_traversal_patterns(tmp_path: Path, pattern: str) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    with pytest.raises(ArtifactSecurityError, match="不得"):
        await ArtifactCollector().collect(str(work_dir), [pattern])


@pytest.mark.asyncio
async def test_collect_detects_replacement_between_match_and_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    artifact_path = work_dir / "result.txt"
    artifact_path.write_text("ok", encoding="utf-8")
    original_hash = collector_module.hash_regular_file

    def replace_before_open(root, relative_path, **kwargs):
        artifact_path.unlink()
        artifact_path.write_text("changed", encoding="utf-8")
        return original_hash(root, relative_path, **kwargs)

    monkeypatch.setattr(collector_module, "hash_regular_file", replace_before_open)

    with pytest.raises(ArtifactSecurityError, match="发生变化"):
        await ArtifactCollector().collect(str(work_dir), ["result.txt"])


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["regular", "symlink", "hardlink"])
async def test_store_rejects_file_replaced_after_collection(tmp_path: Path, replacement: str) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    artifact_path = work_dir / "result.txt"
    artifact_path.write_text("ok", encoding="utf-8")
    store = _store()
    manager = ArtifactManager(artifact_store=store)
    result = await manager.collect_artifacts(str(work_dir), ["result.txt"], "run-1")
    artifact_path.unlink()
    outside = tmp_path / "outside.txt"
    outside.write_text("no", encoding="utf-8")
    if replacement == "regular":
        artifact_path.write_text("no", encoding="utf-8")
    elif replacement == "symlink":
        artifact_path.symlink_to(outside)
    else:
        os.link(outside, artifact_path)

    with pytest.raises(ArtifactSecurityError):
        await manager.store_artifact(result.artifacts[0], "run-1")

    store.upload_task_artifact.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_rejects_in_place_content_change_when_checksum_disabled(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    artifact_path = work_dir / "result.txt"
    artifact_path.write_text("ok", encoding="utf-8")
    collector = ArtifactCollector(ArtifactCollectorConfig(compute_checksum=False))
    store = _store()
    manager = ArtifactManager(collector=collector, artifact_store=store)
    result = await manager.collect_artifacts(str(work_dir), ["result.txt"], "run-1")
    artifact_path.write_text("no", encoding="utf-8")

    with pytest.raises(ArtifactSecurityError):
        await manager.store_artifact(result.artifacts[0], "run-1")

    store.upload_task_artifact.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_detects_content_change_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    artifact_path = work_dir / "result.bin"
    artifact_path.write_bytes(b"a" * (artifact_io_module.READ_CHUNK_SIZE * 2))
    store = _store()
    manager = ArtifactManager(artifact_store=store)
    result = await manager.collect_artifacts(str(work_dir), ["result.bin"], "run-1")
    original_read = artifact_io_module.os.read
    read_count = 0

    def mutate_after_first_read(fd: int, size: int) -> bytes:
        nonlocal read_count
        chunk = original_read(fd, size)
        read_count += 1
        if read_count == 1:
            artifact_path.write_bytes(b"b" * (artifact_io_module.READ_CHUNK_SIZE * 2))
        return chunk

    monkeypatch.setattr(artifact_io_module.os, "read", mutate_after_first_read)

    with pytest.raises(
        ArtifactSecurityError,
        match="(读取过程中发生变化|内容在收集后发生变化)",
    ):
        await manager.store_artifact(result.artifacts[0], "run-1")

    store.upload_task_artifact.assert_not_awaited()


@pytest.mark.asyncio
async def test_collect_rejects_unknown_checksum_algorithm(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "result.txt").write_text("ok", encoding="utf-8")
    collector = ArtifactCollector(ArtifactCollectorConfig(checksum_algorithm="unknown"))

    with pytest.raises(ValueError, match="不支持"):
        await collector.collect(str(work_dir), ["result.txt"])
