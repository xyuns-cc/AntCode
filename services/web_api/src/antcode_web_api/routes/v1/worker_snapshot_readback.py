"""逐列读回 Worker 自报的 metrics / capabilities 快照。

_worker_to_response 是 8 个 Worker 接口（列表、详情、best、render-capable、
my/available、创建、更新、refresh）共用的唯一转换器，所以隔离做在这里而不是在
``get_workers`` 的列表推导里：任何一条路径上，一台 Worker 的一列坏了都只影响
那一列，不影响同一次请求里的其余 Worker，也不影响这台机器的其余字段。

失败不是静默的：坏列置 None + 结构化 ``WorkerSnapshotError`` 进响应体，同时打一条
带 worker_id 与键名的 WARNING —— 响应体是给页面看的，日志是给运维看的，两边都得
指得出"哪台、哪个键"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from antcode_core.domain.schemas.worker import WorkerCapabilities, WorkerMetrics
from antcode_core.domain.schemas.worker_snapshot import (
    CAPABILITIES_COLUMN,
    METRICS_COLUMN,
    WorkerSnapshotError,
)
from loguru import logger
from pydantic import BaseModel, ValidationError

_Snapshot = TypeVar("_Snapshot", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class WorkerSnapshotReadback:
    """一台 Worker 两列快照的读回结果；``errors`` 非空即表示对应列是 None。"""

    metrics: WorkerMetrics | None
    capabilities: WorkerCapabilities | None
    errors: list[WorkerSnapshotError]


def read_worker_snapshot(worker: Any) -> WorkerSnapshotReadback:
    """读回一台 Worker 的 metrics / capabilities 两列，两列互不牵连。"""
    worker_id = str(getattr(worker, "public_id", ""))
    metrics, metrics_error = _read_column(
        WorkerMetrics,
        worker.metrics,
        column=METRICS_COLUMN,
        worker_id=worker_id,
    )
    capabilities, capabilities_error = _read_column(
        WorkerCapabilities,
        worker.capabilities,
        column=CAPABILITIES_COLUMN,
        worker_id=worker_id,
    )
    return WorkerSnapshotReadback(
        metrics=metrics,
        capabilities=capabilities,
        errors=[error for error in (metrics_error, capabilities_error) if error is not None],
    )


def _read_column(
    model: type[_Snapshot],
    raw: Any,
    *,
    column: str,
    worker_id: str,
) -> tuple[_Snapshot | None, WorkerSnapshotError | None]:
    """空列返回空模型（这台机器还没报过），坏列返回 None + 失败详情。"""
    if not raw:
        return model(), None
    try:
        return model(**raw), None
    except ValidationError as exc:
        error = _snapshot_error(exc, column=column)
        logger.warning(
            "Worker 自报快照读回失败，该列已置空并随响应返回: worker_id={} column={} keys={} reason={}",
            worker_id,
            column,
            error.keys,
            error.message,
        )
        return None, error


def _snapshot_error(exc: ValidationError, *, column: str) -> WorkerSnapshotError:
    details = {_error_location(error): str(error.get("msg", "")) for error in exc.errors()}
    keys = sorted(details)
    return WorkerSnapshotError(
        column=column,
        keys=keys,
        message="; ".join(f"{key}: {details[key]}" for key in keys),
    )


def _error_location(error: Any) -> str:
    return ".".join(str(part) for part in error.get("loc", ())) or "<root>"


__all__ = ["WorkerSnapshotReadback", "read_worker_snapshot"]
