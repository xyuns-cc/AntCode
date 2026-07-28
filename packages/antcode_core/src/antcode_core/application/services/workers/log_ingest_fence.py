"""Atomically append logs only for the current execution generation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from antcode_core.application.services.lease_service import LEASE_RECORD_RETENTION_MS, LeaseStore
from antcode_core.application.services.workers.log_ingest_fence_lua import (
    _APPEND_LOG_BATCH_SCRIPT,
)
from antcode_core.application.services.workers.run_ownership_fence import (
    ownership_token,
    run_owner_key,
)
from antcode_core.infrastructure.redis.control_plane import log_ingest_stream_key, redis_namespace

_APPENDED = 1
_RUN_NOT_OWNED = 0
_LEASE_STALE = -1
_APPEND_RESULT_ITEMS = 2


class LogIngestFenceRejected(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"日志代际 fence 拒绝: {reason}")


async def append_fenced_log_batch(
    redis: Any,
    payload: bytes,
    *,
    worker_id: str,
    lease_id: str,
    run_ids: Iterable[str],
    namespace: str | None = None,
) -> str:
    normalized_runs = sorted({run_id for run_id in run_ids if run_id})
    if not normalized_runs:
        raise ValueError("日志批次 run_id 不能为空")
    ns = redis_namespace(namespace)
    keys = [
        LeaseStore.LEASE_KEY_TEMPLATE.format(ns=ns, worker_id=worker_id),
        log_ingest_stream_key(ns),
        *(run_owner_key(run_id, ns) for run_id in normalized_runs),
    ]
    result = await redis.eval(
        _APPEND_LOG_BATCH_SCRIPT,
        len(keys),
        *keys,
        worker_id,
        lease_id,
        ownership_token(worker_id, lease_id),
        LEASE_RECORD_RETENTION_MS,
        payload,
    )
    return _parse_append_result(result)


def _parse_append_result(result: Any) -> str:
    if not isinstance(result, (list, tuple)) or len(result) != _APPEND_RESULT_ITEMS:
        raise RuntimeError(f"日志 fence Lua 返回非法: {result!r}")
    code = int(result[0])
    detail = result[1].decode() if isinstance(result[1], bytes) else str(result[1])
    if code == _APPENDED and detail:
        return detail
    if code == _LEASE_STALE:
        raise LogIngestFenceRejected("lease_stale")
    if code == _RUN_NOT_OWNED:
        raise LogIngestFenceRejected("run_not_owned")
    raise RuntimeError(f"日志 fence Lua 返回未知结果: {result!r}")


__all__ = ["LogIngestFenceRejected", "append_fenced_log_batch"]
