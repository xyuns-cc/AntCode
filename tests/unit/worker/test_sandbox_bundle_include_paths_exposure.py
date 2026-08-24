"""include_paths 共享目录必须在沙箱内可见，且不得因此暴露 bundle 之外的路径。

钉死的缺陷：`sandbox_filesystem_args` 只 bind `work_dir`（= `extracted/<subdir>`），
把 `extracted/` 建成空 `--dir`，于是 source bundle 里由 include_paths 带进来的共享
目录在沙箱内根本不存在——功能整条链路空转。
"""

from __future__ import annotations

import asyncio
import io
import sys
import zipfile
from hashlib import sha256
from pathlib import Path

import pytest
from antcode_worker.executor.sandbox_mounts import SandboxFilesystemRequest, sandbox_filesystem_args
from antcode_worker.projects.fetcher import ArtifactFetcher, ProjectWorkspace

RUN_ID = "run-current"
PROJECT_ID = "proj-1"
CONTENT_HASH = "b" * 64
SHARED_MODULE = "libs/common/helper.py"
ENTRY_MODULE = "src/main.py"


@pytest.fixture(autouse=True)
def _trust_current_uv_python(monkeypatch: pytest.MonkeyPatch) -> None:
    install_root = Path(sys.executable).resolve().parent.parent
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(install_root.parent))


class _ArtifactStore:
    def __init__(self, blob: bytes):
        self._blob = blob

    async def download_source_bundle(self, request, destination: Path) -> None:
        del request
        destination.write_bytes(self._blob)


def _bundle_blob() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(SHARED_MODULE, "BANNER = 'SHARED-OK'\n")
        archive.writestr(ENTRY_MODULE, "print('ok')\n")
    return buffer.getvalue()


class _Layout:
    """真实解包布局 data_root/runs/sources/<run>/<project>/<hash>/extracted。"""

    def __init__(self, root: Path):
        self.data_root = root / "worker"
        self.runtimes_root = self.data_root / "runtimes"
        self.sources_root = self.data_root / "runs" / "sources"
        self.project_dir = self.sources_root / RUN_ID / PROJECT_ID / CONTENT_HASH
        self.bundle_root = self.project_dir / "extracted"
        self.work_dir = self.bundle_root / "src"
        self.shared_dir = self.bundle_root / "libs" / "common"

    def materialize(self) -> None:
        for path in (self.runtimes_root, self.work_dir, self.shared_dir):
            path.mkdir(parents=True, exist_ok=True)

    def request(self, **overrides) -> SandboxFilesystemRequest:
        fields: dict[str, object] = {
            "work_dir": self.work_dir,
            "payload_executable": sys.executable,
            "data_root": self.data_root,
            "runtimes_root": self.runtimes_root,
            "run_id": RUN_ID,
            "bundle_root": self.bundle_root,
            "tmpfs_size_mb": 512,
        }
        fields.update(overrides)
        return SandboxFilesystemRequest(**fields)  # type: ignore[arg-type]


def _mount_pairs(args: tuple[str, ...], option: str) -> set[tuple[str, str]]:
    return {(args[index + 1], args[index + 2]) for index, value in enumerate(args[:-2]) if value == option}


def _mount_index(args: tuple[str, ...], option: str, source: Path) -> int:
    for index, value in enumerate(args[:-2]):
        if value == option and args[index + 1] == str(source):
            return index
    raise AssertionError(f"沙箱参数里没有 {option} {source}")


def _visible_at_same_path(mounts: set[tuple[str, str]], target: Path) -> bool:
    """宿主路径 target 是否被某个 src==dest 的挂载原样带进沙箱。"""
    return any(
        source == destination and (target == Path(source) or Path(source) in target.parents)
        for source, destination in mounts
    )


def test_include_paths_sibling_directory_is_visible_inside_the_sandbox(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    layout.materialize()

    args = sandbox_filesystem_args(layout.request())

    read_only = _mount_pairs(args, "--ro-bind")
    writable = _mount_pairs(args, "--bind")
    assert _visible_at_same_path(read_only | writable, layout.shared_dir)
    assert (str(layout.work_dir), str(layout.work_dir)) in writable


def test_shared_directory_is_read_only_while_project_dir_stays_writable(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    layout.materialize()

    args = sandbox_filesystem_args(layout.request())

    assert not _visible_at_same_path(_mount_pairs(args, "--bind"), layout.shared_dir)
    assert _visible_at_same_path(_mount_pairs(args, "--ro-bind"), layout.shared_dir)
    # bwrap 按顺序建挂载：只读的 bundle 根必须先于可写的项目目录，
    # 否则可写 bind 会被随后的只读 bind 盖掉，任务写不了产物。
    assert _mount_index(args, "--ro-bind", layout.bundle_root) < _mount_index(args, "--bind", layout.work_dir)


def test_bundle_exposure_does_not_leak_sibling_runs_or_projects(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    layout.materialize()
    other_project = layout.sources_root / RUN_ID / "proj-2" / CONTENT_HASH / "extracted"
    other_run = layout.sources_root / "run-other" / PROJECT_ID / CONTENT_HASH / "extracted"
    stale_bundle = layout.project_dir / "source.bundle"
    for path in (other_project, other_run):
        path.mkdir(parents=True)
    stale_bundle.write_bytes(b"archive")

    args = sandbox_filesystem_args(layout.request())

    mounts = _mount_pairs(args, "--ro-bind") | _mount_pairs(args, "--bind")
    for leaked in (other_project, other_run, stale_bundle):
        assert not _visible_at_same_path(mounts, leaked)


def test_extracted_bundle_layout_is_exposed_end_to_end(tmp_path: Path) -> None:
    """用真实 ArtifactFetcher 解包，确认沙箱认得它产出的布局。"""
    data_root = tmp_path / "worker"
    runtimes_root = data_root / "runtimes"
    runtimes_root.mkdir(parents=True)
    blob = _bundle_blob()
    fetcher = ArtifactFetcher(
        workspace=ProjectWorkspace(root_dir=str(data_root / "runs" / "sources")),
        artifact_store=_ArtifactStore(blob),
    )
    digest = sha256(blob).hexdigest()

    workspace = asyncio.run(
        fetcher.fetch(
            run_id=RUN_ID,
            project_id=PROJECT_ID,
            source_bundle_uri=f"pgartifact://{digest}",
            source_bundle_sha256=digest,
            source_bundle_size=len(blob),
            source_subdir="src",
        )
    )
    shared_module = Path(workspace.bundle_root, SHARED_MODULE)
    assert shared_module.is_file()

    args = sandbox_filesystem_args(
        SandboxFilesystemRequest(
            work_dir=Path(workspace.project_cwd),
            payload_executable=sys.executable,
            data_root=data_root,
            runtimes_root=runtimes_root,
            run_id=RUN_ID,
            bundle_root=Path(workspace.bundle_root),
            tmpfs_size_mb=512,
        )
    )

    mounts = _mount_pairs(args, "--ro-bind") | _mount_pairs(args, "--bind")
    assert _visible_at_same_path(mounts, shared_module.parent)


def test_bundle_root_outside_worker_data_is_rejected(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    layout.materialize()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(RuntimeError, match="source bundle 根目录不在当前 run 的 source workspace"):
        sandbox_filesystem_args(layout.request(bundle_root=outside))


@pytest.mark.parametrize("relative", ["..", "../..", "../../.."])
def test_bundle_root_traversal_above_extracted_is_rejected(tmp_path: Path, relative: str) -> None:
    layout = _Layout(tmp_path)
    layout.materialize()

    with pytest.raises(RuntimeError, match="source bundle 根目录不是 run 的解包目录"):
        sandbox_filesystem_args(layout.request(bundle_root=layout.bundle_root / relative))


def test_bundle_root_of_another_run_is_rejected(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    layout.materialize()
    foreign = layout.sources_root / "run-other" / PROJECT_ID / CONTENT_HASH / "extracted"
    foreign.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="source bundle 根目录不属于当前 run"):
        sandbox_filesystem_args(layout.request(bundle_root=foreign))


def test_bundle_root_must_contain_the_work_dir(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    layout.materialize()
    sibling = layout.sources_root / RUN_ID / "proj-2" / CONTENT_HASH / "extracted"
    sibling.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="必须是任务工作目录的上级"):
        sandbox_filesystem_args(layout.request(bundle_root=sibling))


def test_bundle_root_symlink_escape_is_rejected(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    layout.materialize()
    escape = layout.project_dir / "extracted-alias"
    escape.symlink_to(tmp_path / "etc")
    (tmp_path / "etc").mkdir()

    with pytest.raises(RuntimeError, match="source bundle 根目录不在当前 run 的 source workspace"):
        sandbox_filesystem_args(layout.request(bundle_root=escape))


def test_missing_bundle_root_fails_closed(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    layout.materialize()

    with pytest.raises(RuntimeError, match="source bundle 根目录不可用"):
        sandbox_filesystem_args(layout.request(bundle_root=layout.project_dir / "gone"))


def test_bundle_root_file_instead_of_directory_is_rejected(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    layout.materialize()
    impostor = layout.project_dir / "extracted.tar"
    impostor.write_bytes(b"archive")

    with pytest.raises(RuntimeError, match="source bundle 根目录不是目录"):
        sandbox_filesystem_args(layout.request(bundle_root=impostor))
