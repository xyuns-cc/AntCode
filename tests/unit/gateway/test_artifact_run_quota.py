"""P1-SEC-04: Gateway 上传的 per-run artifact 配额。

此前仅有单文件 100MiB 上限，持有有效 Lease 的受控 Worker 可无限堆积
artifact 耗尽存储。修复后按 run 维度限制数量与总字节（与 worker 端
``artifact_collector`` 默认上限一致），超限返回 RESOURCE_EXHAUSTED。
"""

from __future__ import annotations

import grpc
import pytest
from antcode_gateway.services.artifact_quota import (
    MAX_ARTIFACT_BYTES_PER_RUN,
    MAX_ARTIFACTS_PER_RUN,
    RunArtifactQuota,
)

from tests.unit.gateway.artifact_service_helpers import (
    FakeArtifactStore,
    make_context,
    make_service,
    make_upload_frames,
    run_upload,
)


@pytest.mark.asyncio
async def test_upload_rejects_run_over_artifact_count_quota():
    """单 run artifact 数量配额耗尽后必须 RESOURCE_EXHAUSTED。"""
    store = FakeArtifactStore()
    service = make_service(store, run_quota=RunArtifactQuota(max_artifacts=1))
    await run_upload(service, make_upload_frames(b"first"), make_context())

    context = make_context()
    with pytest.raises(grpc.aio.AbortError):
        await run_upload(service, make_upload_frames(b"second"), context)

    assert context.abort.await_args.args[0] == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert [content for content, _media in store.writes] == [b"first"]


@pytest.mark.asyncio
async def test_upload_rejects_run_over_total_bytes_quota():
    """单 run 总字节配额按声明大小在收流前预留。"""
    store = FakeArtifactStore()
    service = make_service(store, run_quota=RunArtifactQuota(max_total_bytes=10))
    await run_upload(service, make_upload_frames(b"a" * 8), make_context())

    context = make_context()
    with pytest.raises(grpc.aio.AbortError):
        await run_upload(service, make_upload_frames(b"b" * 3), context)

    assert context.abort.await_args.args[0] == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert [content for content, _media in store.writes] == [b"a" * 8]


@pytest.mark.asyncio
async def test_upload_quota_is_scoped_per_run():
    store = FakeArtifactStore()
    service = make_service(store, run_quota=RunArtifactQuota(max_artifacts=1))
    await run_upload(service, make_upload_frames(b"run-a-artifact"), make_context())

    response = await run_upload(service, make_upload_frames(b"run-b-artifact", run_id="run-b"), make_context())

    assert response.size_bytes == len(b"run-b-artifact")
    assert [content for content, _media in store.writes] == [b"run-a-artifact", b"run-b-artifact"]


@pytest.mark.asyncio
async def test_failed_upload_releases_run_quota():
    """上传失败（如 sha256 证据不符）必须归还预留额度，不吞掉合法配额。"""
    store = FakeArtifactStore()
    quota = RunArtifactQuota(max_artifacts=1)
    service = make_service(store, run_quota=quota)

    bad_frames = make_upload_frames(b"payload", sha256="a" * 64)
    with pytest.raises(grpc.aio.AbortError):
        await run_upload(service, bad_frames, make_context())
    assert quota.usage_of("run-a") == (0, 0)

    response = await run_upload(service, make_upload_frames(b"payload"), make_context())

    assert response.size_bytes == len(b"payload")


@pytest.mark.asyncio
async def test_quota_rejection_happens_before_receiving_chunks():
    """配额拒绝必须发生在存储写入之前（不接收、不落盘）。"""
    store = FakeArtifactStore()
    quota = RunArtifactQuota(max_total_bytes=1)
    context = make_context()

    with pytest.raises(grpc.aio.AbortError):
        await run_upload(make_service(store, run_quota=quota), make_upload_frames(b"too-big"), context)

    assert context.abort.await_args.args[0] == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert store.writes == []
    assert quota.usage_of("run-a") == (0, 0)


def test_run_quota_defaults_align_with_worker_collector_limits():
    """服务端默认配额与 worker 端 artifact_collector 的默认上限一致。"""
    from antcode_worker.executor.artifact_collector import (
        DEFAULT_MAX_FILE_COUNT,
        DEFAULT_MAX_TOTAL_SIZE,
    )

    assert MAX_ARTIFACTS_PER_RUN == DEFAULT_MAX_FILE_COUNT
    assert MAX_ARTIFACT_BYTES_PER_RUN == DEFAULT_MAX_TOTAL_SIZE


def test_run_quota_ledger_evicts_oldest_runs():
    quota = RunArtifactQuota(max_tracked_runs=2)
    quota.reserve("run-1", 1)
    quota.reserve("run-2", 1)
    quota.reserve("run-3", 1)

    assert quota.usage_of("run-1") == (0, 0)
    assert quota.usage_of("run-2") == (1, 1)
    assert quota.usage_of("run-3") == (1, 1)


def test_run_quota_release_never_goes_negative():
    quota = RunArtifactQuota()
    quota.reserve("run-1", 4)
    quota.release("run-1", 8)
    quota.release("run-unknown", 8)

    assert quota.usage_of("run-1") == (0, 0)
    assert quota.usage_of("run-unknown") == (0, 0)
