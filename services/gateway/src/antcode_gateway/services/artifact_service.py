"""Authenticated artifact transfer for Gateway-isolated Workers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

import aiofiles  # type: ignore[import-untyped]
import grpc
from antcode_contracts import artifact_pb2
from antcode_contracts.artifact_pb2_grpc import ArtifactServiceServicer
from antcode_core.application.services.workers.run_ownership_service import (
    require_worker_owns_runs_for_lease,
)
from antcode_core.domain.models.task_status_sets import SPIDER_WRITABLE_TASK_STATUSES
from loguru import logger

from antcode_gateway.auth import require_authenticated_worker
from antcode_gateway.services.artifact_quota import (
    RunArtifactQuota,
    RunArtifactQuotaExceeded,
)
from antcode_gateway.services.artifact_transfer import (
    ARTIFACT_CHUNK_BYTES,
    MAX_ARTIFACT_BYTES,
    read_upload_metadata,
    receive_upload,
    require_upload_evidence,
    temporary_path,
    validate_download_request,
    validate_upload_metadata,
)
from antcode_gateway.services.run_ownership_rpc import RunOwnershipRpcMixin

LeaseVerifier = Callable[[str, str], Awaitable[bool]]
OwnershipVerifier = Callable[[str, str, str], Awaitable[None]]
SourceAuthorizer = Callable[[str, str, str], Awaitable[None]]


class ArtifactStore(Protocol):
    async def read_blob_to_file(
        self,
        content_hash: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> Any: ...

    async def write_blob(
        self,
        content: bytes,
        media_type: str = "application/octet-stream",
        metadata: dict[str, object] | None = None,
    ) -> Any: ...

    async def write_blob_from_file(
        self,
        source: Path,
        media_type: str = "application/octet-stream",
        metadata: dict[str, object] | None = None,
    ) -> Any: ...


async def _missing_lease_verifier(_worker_id: str, _lease_id: str) -> bool:
    raise RuntimeError("Gateway artifact lease verifier 未配置")


async def _authorize_source_snapshot(run_id: str, project_id: str, sha256: str) -> None:
    from antcode_core.domain.models import Project, RunSourceSnapshot

    project = await Project.get_or_none(public_id=project_id)
    if project is None:
        raise PermissionError("source bundle 不属于请求的 run")
    allowed = await RunSourceSnapshot.filter(
        run_id=run_id,
        project_id=project.id,
        artifact_sha256=sha256,
    ).exists()
    if not allowed:
        raise PermissionError("source bundle 不属于请求的 run")


async def _require_worker_owns_active_run(worker_id: str, run_id: str, lease_id: str) -> None:
    from antcode_core.domain.models import TaskRun

    await require_worker_owns_runs_for_lease(worker_id, [run_id], lease_id=lease_id)
    active = await TaskRun.filter(
        run_id=run_id,
        status__in=SPIDER_WRITABLE_TASK_STATUSES,
    ).exists()
    if not active:
        raise PermissionError("TaskRun 已进入终态")


class GatewayArtifactService(RunOwnershipRpcMixin, ArtifactServiceServicer):
    """Streams immutable source bundles and task artifacts through Gateway."""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        lease_verifier: LeaseVerifier = _missing_lease_verifier,
        ownership_verifier: OwnershipVerifier = _require_worker_owns_active_run,
        source_authorizer: SourceAuthorizer = _authorize_source_snapshot,
        run_quota: RunArtifactQuota | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._lease_verifier = lease_verifier
        self._ownership_verifier = ownership_verifier
        self._source_authorizer = source_authorizer
        # P1-SEC-04: per-run 上传配额（数量 + 总字节），防止持有有效 Lease
        # 的受控 Worker 以 100MiB/件无限堆积耗尽存储。
        self._run_quota = run_quota if run_quota is not None else RunArtifactQuota()

    async def DownloadSourceBundle(
        self,
        request: artifact_pb2.SourceBundleDownloadRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[artifact_pb2.ArtifactChunk]:
        worker_id = await require_authenticated_worker(context, request.worker_id)
        digest = await validate_download_request(request, context)
        await self._require_access(
            context,
            worker_id=worker_id,
            lease_id=request.lease_id,
            run_id=request.run_id,
        )
        await self._require_source_access(
            context,
            run_id=request.run_id,
            project_id=request.project_id,
            digest=digest,
        )

        path = temporary_path()
        try:
            stored = await self._stage_download(
                context,
                digest=digest,
                expected_size=request.size_bytes,
                path=path,
            )
            await self._require_access(
                context,
                worker_id=worker_id,
                lease_id=request.lease_id,
                run_id=request.run_id,
            )
            async for chunk in self._read_download(
                path,
                worker_id=worker_id,
                lease_id=request.lease_id,
                context=context,
            ):
                yield chunk
            logger.info(
                "source bundle 下载完成: worker={} run={} bytes={}", worker_id, request.run_id, stored.size_bytes
            )
        finally:
            path.unlink(missing_ok=True)

    async def UploadTaskArtifact(
        self,
        request_iterator: AsyncIterator[artifact_pb2.ArtifactUploadFrame],
        context: grpc.aio.ServicerContext,
    ) -> artifact_pb2.ArtifactUploadResponse:
        metadata = await read_upload_metadata(request_iterator, context)
        worker_id = await require_authenticated_worker(context, metadata.worker_id)
        digest = await validate_upload_metadata(metadata, context)
        await self._require_access(
            context,
            worker_id=worker_id,
            lease_id=metadata.lease_id,
            run_id=metadata.run_id,
        )
        # P1-SEC-04: 收流前按声明大小预留 per-run 配额（声明与实际的一致性
        # 由上传证据强制），超限即 RESOURCE_EXHAUSTED；失败路径归还额度。
        await self._reserve_run_quota(context, run_id=metadata.run_id, size_bytes=metadata.size_bytes)

        path = temporary_path()
        try:
            actual_size, actual_hash = await receive_upload(request_iterator, context, path)
            try:
                require_upload_evidence(metadata, actual_size, actual_hash)
            except ValueError as exc:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
            await self._require_access(
                context,
                worker_id=worker_id,
                lease_id=metadata.lease_id,
                run_id=metadata.run_id,
            )
            stored = await self._store_upload(
                context,
                path=path,
                media_type=metadata.media_type,
                digest=digest,
            )
            logger.info("task artifact 上传完成: worker={} run={} bytes={}", worker_id, metadata.run_id, actual_size)
            return artifact_pb2.ArtifactUploadResponse(
                uri=stored.uri,
                sha256=stored.content_hash,
                size_bytes=stored.size_bytes,
                media_type=stored.media_type,
            )
        except BaseException:
            # P1-round6 5.3: 走 async_release, 归还内存 + Redis 备份 (二者同步)。
            # 无 redis_client 场景退化为纯内存 release, 兼容单元测试与 dev。
            await self._run_quota.async_release(metadata.run_id, metadata.size_bytes)
            raise
        finally:
            path.unlink(missing_ok=True)

    async def _reserve_run_quota(self, context, *, run_id: str, size_bytes: int) -> None:
        # P1-round6 5.3: 走 async_reserve, 先 restore_from_redis (幂等) 再内存
        # fence + Redis 备份写。Gateway 重启后 restore 能防"重启放大 quota"。
        try:
            await self._run_quota.async_reserve(run_id, size_bytes)
        except RunArtifactQuotaExceeded as exc:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, str(exc))

    async def _require_access(self, context, *, worker_id: str, lease_id: str, run_id: str) -> None:
        if not lease_id:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "worker lease is not current")
        try:
            if not await self._lease_verifier(worker_id, lease_id):
                await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "worker lease is not current")
            await self._ownership_verifier(worker_id, run_id, lease_id)
        except grpc.aio.AbortError:
            raise
        except (PermissionError, ValueError) as exc:
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, str(exc))
        except Exception:
            logger.exception("artifact access 校验失败")
            await context.abort(grpc.StatusCode.UNAVAILABLE, "artifact access verification unavailable")

    async def _require_source_access(self, context, *, run_id: str, project_id: str, digest: str) -> None:
        try:
            await self._source_authorizer(run_id, project_id, digest)
        except (PermissionError, ValueError):
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, "source bundle 不属于请求的 run")
        except grpc.aio.AbortError:
            raise
        except Exception:
            logger.exception("source bundle ownership 校验失败")
            await context.abort(grpc.StatusCode.UNAVAILABLE, "source bundle verification unavailable")

    async def _stage_download(self, context, *, digest: str, expected_size: int, path: Path) -> Any:
        try:
            stored = await self._artifact_store.read_blob_to_file(digest, path, max_bytes=MAX_ARTIFACT_BYTES)
        except FileNotFoundError:
            await context.abort(grpc.StatusCode.NOT_FOUND, "source bundle 不存在")
        except ValueError:
            await context.abort(grpc.StatusCode.DATA_LOSS, "source bundle 内容损坏")
        except grpc.aio.AbortError:
            raise
        except Exception:
            logger.exception("source bundle 读取失败")
            await context.abort(grpc.StatusCode.UNAVAILABLE, "source bundle storage unavailable")
        if stored.content_hash != digest or stored.size_bytes != expected_size:
            await context.abort(grpc.StatusCode.DATA_LOSS, "source bundle 元数据不一致")
        return stored

    async def _read_download(self, path: Path, *, worker_id: str, lease_id: str, context):
        offset = 0
        async with aiofiles.open(path, "rb") as source:
            while data := await source.read(ARTIFACT_CHUNK_BYTES):
                try:
                    current = await self._lease_verifier(worker_id, lease_id)
                except Exception:
                    logger.exception("artifact stream lease 校验失败")
                    await context.abort(grpc.StatusCode.UNAVAILABLE, "lease verification unavailable")
                if not current:
                    await context.abort(
                        grpc.StatusCode.FAILED_PRECONDITION,
                        "worker lease is not current",
                    )
                yield artifact_pb2.ArtifactChunk(offset=offset, data=data)
                offset += len(data)

    async def _store_upload(self, context, *, path: Path, media_type: str, digest: str) -> Any:
        # P2 §4.2: 流式落库（O(chunk) 内存），不再整读文件再二次切片。
        try:
            expected_size = path.stat().st_size
            stored = await self._artifact_store.write_blob_from_file(path, media_type=media_type)
        except Exception:
            logger.exception("task artifact 写入失败")
            await context.abort(grpc.StatusCode.UNAVAILABLE, "artifact storage unavailable")
        if stored.content_hash != digest or stored.size_bytes != expected_size:
            await context.abort(grpc.StatusCode.DATA_LOSS, "artifact storage evidence mismatch")
        return stored


__all__ = ["GatewayArtifactService"]
