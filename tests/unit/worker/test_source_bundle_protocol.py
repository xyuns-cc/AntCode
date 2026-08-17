"""Worker source bundle 协议测试。"""

from __future__ import annotations

import io
import json
import tarfile
import zipfile
from dataclasses import fields
from hashlib import sha256
from pathlib import Path

import pytest
from antcode_contracts import data_pb2
from antcode_core.common.security.task_payload_envelope import seal_ready_payload
from antcode_worker.artifact_transfer import SourceBundleDownload
from antcode_worker.domain.models import ArtifactRef, RunContext, RuntimeSpec, TaskPayload
from antcode_worker.plugins.code.plugin import CodePlugin
from antcode_worker.projects.fetcher import ArtifactFetcher, ProjectWorkspace
from antcode_worker.transport.base import TaskMessage
from antcode_worker.transport.gateway.codecs import CodecError, TaskDecoder
from antcode_worker.transport.redis.codecs import CodecError as RedisCodecError
from antcode_worker.transport.redis.codecs import JsonCodec


class _ArtifactStore:
    def __init__(self, blob: bytes):
        self._blob = blob
        self.read_hashes: list[str] = []

    async def download_source_bundle(self, request: SourceBundleDownload, destination: Path) -> None:
        self.read_hashes.append(request.content_hash)
        assert len(self._blob) <= request.max_bytes
        destination.write_bytes(self._blob)


def _zip_blob() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("spiders/news/main.py", "print('ok')\n")
    return buffer.getvalue()


def _repo_bundle_blob() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("libs/common/helpers.py", "VALUE = 1\n")
        archive.writestr("spiders/news/main.py", "print('ok')\n")
    return buffer.getvalue()


def _tar_symlink_blob() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo("project/escape")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        archive.addfile(member)
    return buffer.getvalue()


def test_task_models_only_expose_source_bundle_fields():
    message_fields = {field.name for field in fields(TaskMessage)}
    payload_fields = {field.name for field in fields(TaskPayload)}

    assert "download_url" not in message_fields
    assert "file_hash" not in message_fields
    assert "download_url" not in payload_fields
    assert "file_hash" not in payload_fields
    assert "source_bundle" in message_fields
    assert "source_bundle" in payload_fields


def test_redis_task_payload_decoder_rejects_project_path():
    codec = JsonCodec()

    with pytest.raises(Exception, match="project_path"):
        codec.decode(
            {
                "_schema_version": "v1",
                "project_path": '"/tmp/legacy"',
                "source_bundle": '{"uri":"pgartifact://' + "a" * 64 + '","sha256":"' + "a" * 64 + '","size":1}',
            },
            TaskPayload,
        )


def test_redis_task_payload_decoder_requires_schema_version():
    codec = JsonCodec()

    with pytest.raises(RedisCodecError, match="_schema_version"):
        codec.decode(
            {
                "task_type": "code",
                "source_bundle": '{"uri":"pgartifact://' + "a" * 64 + '","sha256":"' + "a" * 64 + '","size":1}',
                "entry_point": "main.py",
            },
            TaskPayload,
        )


def test_redis_codec_has_no_schema_migration_fallbacks():
    source = Path("services/worker/src/antcode_worker/transport/redis/codecs.py").read_text(encoding="utf-8")

    assert "_migrate_schema" not in source
    assert "SchemaVersion.V2" not in source
    assert "SchemaVersion.V1.value)" not in source


def test_redis_artifact_decoder_rejects_local_path():
    codec = JsonCodec()

    with pytest.raises(RedisCodecError, match="local_path"):
        codec.decode(
            {
                "_schema_version": "v1",
                "name": "result.txt",
                "type": "file",
                "uri": "pgartifact://" + "a" * 64,
                "local_path": "/tmp/result.txt",
            },
            ArtifactRef,
        )


def _gateway_dispatch(**updates):
    payload = {
        "task_id": "task-1",
        "project_id": "proj-1",
        "run_id": "run-1",
        "project_type": "code",
        "timeout": 30,
        "params": {},
        "environment": {},
        **updates,
    }
    sealed = seal_ready_payload(
        payload,
        worker_id="worker-1",
        worker_secret="gateway-source-bundle-secret-material-0001",
    )
    return data_pb2.TaskDispatch(
        task_id="task-1",
        sealed_ready_payload=json.dumps(sealed, separators=(",", ":"), sort_keys=True).encode(),
    )


def test_gateway_task_decoder_requires_pgartifact_source_bundle():
    with pytest.raises(CodecError, match="pgartifact"):
        TaskDecoder.decode(
            _gateway_dispatch(),
            worker_id="worker-1",
            worker_secret="gateway-source-bundle-secret-material-0001",
        )

    with pytest.raises(CodecError, match="pgartifact"):
        TaskDecoder.decode(
            _gateway_dispatch(
                source_bundle_uri="git+https://example.com/repo.git",
                source_bundle_sha256="a" * 64,
            ),
            worker_id="worker-1",
            worker_secret="gateway-source-bundle-secret-material-0001",
        )


@pytest.mark.asyncio
async def test_artifact_fetcher_rejects_git_uri(tmp_path):
    fetcher = ArtifactFetcher(
        workspace=ProjectWorkspace(root_dir=str(tmp_path / "runs")),
        artifact_store=_ArtifactStore(b""),
    )

    with pytest.raises(ValueError, match="pgartifact"):
        await fetcher.fetch(
            run_id="run-1",
            project_id="proj-1",
            source_bundle_uri="git+https://example.com/repo.git",
            source_bundle_sha256="a" * 64,
            source_bundle_size=0,
        )


@pytest.mark.asyncio
async def test_artifact_fetcher_reads_pgartifact_blob_and_verifies_sha256(tmp_path):
    blob = _zip_blob()
    digest = sha256(blob).hexdigest()
    store = _ArtifactStore(blob)
    fetcher = ArtifactFetcher(
        workspace=ProjectWorkspace(root_dir=str(tmp_path / "runs")),
        artifact_store=store,
    )

    workspace = await fetcher.fetch(
        run_id="run-1",
        project_id="proj-1",
        source_bundle_uri=f"pgartifact://{digest}",
        source_bundle_sha256=digest,
        source_bundle_size=len(blob),
        source_subdir="spiders/news",
    )

    assert store.read_hashes == [digest]
    assert Path(workspace.bundle_root, "spiders/news/main.py").read_text() == "print('ok')\n"
    assert not (tmp_path / "runs" / "index.json").exists()


@pytest.mark.asyncio
async def test_artifact_fetcher_rejects_internal_symlink(tmp_path):
    blob = _tar_symlink_blob()
    digest = sha256(blob).hexdigest()
    fetcher = ArtifactFetcher(
        workspace=ProjectWorkspace(root_dir=str(tmp_path / "runs")),
        artifact_store=_ArtifactStore(blob),
    )

    with pytest.raises(ValueError, match="不安全的压缩路径"):
        await fetcher.fetch(
            run_id="run-1",
            project_id="proj-1",
            source_bundle_uri=f"pgartifact://{digest}",
            source_bundle_sha256=digest,
            source_bundle_size=len(blob),
            source_subdir="project",
        )


@pytest.mark.asyncio
async def test_artifact_fetcher_returns_project_cwd_and_bundle_root(tmp_path):
    blob = _repo_bundle_blob()
    digest = sha256(blob).hexdigest()
    fetcher = ArtifactFetcher(
        workspace=ProjectWorkspace(root_dir=str(tmp_path / "runs")),
        artifact_store=_ArtifactStore(blob),
    )

    workspace = await fetcher.fetch(
        run_id="run-1",
        project_id="proj-1",
        source_bundle_uri=f"pgartifact://{digest}",
        source_bundle_sha256=digest,
        source_bundle_size=len(blob),
        source_subdir="spiders/news",
    )

    assert workspace.bundle_root == str(Path(workspace.bundle_root))
    assert Path(workspace.bundle_root, "libs/common/helpers.py").exists()
    assert Path(workspace.project_cwd, "main.py").read_text() == "print('ok')\n"


@pytest.mark.asyncio
async def test_code_plugin_runs_from_project_cwd_and_leaves_pythonpath_to_the_executor():
    """插件只负责 cwd；PYTHONPATH 归 executor.python_path 独占。

    插件写的 PYTHONPATH 会被 ProcessExecutor._build_env 原样盖掉，断言它等于
    断言一个到不了子进程的中间值。子进程侧的真实契约见
    tests/unit/worker/test_child_process_pythonpath.py。
    """
    plugin = CodePlugin()
    payload = TaskPayload(
        entry_point="main.py",
        workspace_path="/data/workers/w1/runs/r1/bundle",
        project_cwd="/data/workers/w1/runs/r1/bundle/spiders/news",
    )

    context = RunContext(
        run_id="run-1",
        task_id="task-1",
        project_id="proj-1",
        runtime_spec=RuntimeSpec(python_path="/opt/antcode/runtime/bin/python"),
    )
    plan = await plugin.build_plan(context=context, payload=payload)

    assert plan.cwd == "/data/workers/w1/runs/r1/bundle/spiders/news"
    assert "PYTHONPATH" not in plan.env


@pytest.mark.asyncio
async def test_artifact_fetcher_cleans_run_workspace(tmp_path):
    blob = _zip_blob()
    digest = sha256(blob).hexdigest()
    fetcher = ArtifactFetcher(
        workspace=ProjectWorkspace(root_dir=str(tmp_path / "runs")),
        artifact_store=_ArtifactStore(blob),
    )

    await fetcher.fetch(
        run_id="run-1",
        project_id="proj-1",
        source_bundle_uri=f"pgartifact://{digest}",
        source_bundle_sha256=digest,
        source_bundle_size=len(blob),
        source_subdir="spiders/news",
    )

    await fetcher.cleanup("run-1")

    assert not (tmp_path / "runs" / "run-1").exists()
