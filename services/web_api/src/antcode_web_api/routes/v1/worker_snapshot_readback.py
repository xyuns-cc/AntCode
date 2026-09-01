"""逐列读回 Worker 自报的 metrics / capabilities 快照。

_worker_to_response 是 8 个 Worker 接口（列表、详情、best、render-capable、
my/available、创建、更新、refresh）共用的唯一转换器，所以隔离做在这里而不是在
``get_workers`` 的列表推导里：任何一条路径上，一台 Worker 的一列坏了都只影响
那一列，不影响同一次请求里的其余 Worker，也不影响这台机器的其余字段。

失败不是静默的：坏列置 None + 结构化 ``WorkerSnapshotError`` 进响应体，同时打一条
带 worker_id 与键名的 WARNING —— 响应体是给页面看的，日志是给运维看的，两边都得
指得出"哪台、哪个键"。

这一列可能坏成三种样子，三种的含义与处置都不同，塌成一种就等于把 bug 藏起来：

1. **空列**（NULL / ``{}``）：这台机器还没上报过。返回 ``None`` 且不带
   ``WorkerSnapshotError``——这正是前端 ``WorkerMetricCell`` 约定的"还没心跳"分支。
   曾经这里返回全默认的 ``WorkerMetrics()``（cpu 0 / 并发 5），与一台真正空闲的
   Worker 逐字节相同，而 ``worker_snapshot`` 的模块注释自己就写着这不可接受；
2. **字段漂移**：整列还是 JSON 对象，但键集或取值不满足读回模型 → ``ValidationError``；
3. **结构坏了**：整列压根不是 JSON 对象（jsonb 里存进了数组，或被二次编码成字符串）。
   ``model(**raw)`` 这时抛的是 **TypeError**，pydantic 不接管，于是整个 ``GET /workers``
   打成 500（真机 mn 栈实测，traceback 里既没有 worker_id 也没有列名）——与 eb7199a
   "``for rule in v`` 抛 TypeError 而 pydantic 只接管 ValueError"是同一种错配。

第 3 种在这里用显式的 ``Mapping`` 形状检查挡掉，而不是去 ``except TypeError``：这一列
的契约本来就是"必须是 JSON 对象"，把它写成断言比依赖 CPython 的 ``**`` 报错更准，也不会
顺手吞掉模型内部真正的 TypeError。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from antcode_core.domain.schemas.worker import WorkerCapabilities, WorkerMetrics
from antcode_core.domain.schemas.worker_snapshot import (
    CAPABILITIES_COLUMN,
    METRICS_COLUMN,
    SnapshotErrorReason,
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
    """空列返回 (None, None)（这台机器还没报过），坏列返回 None + 失败详情。"""
    if not raw:
        return None, None
    if not isinstance(raw, Mapping):
        return None, _log_and_return(_structure_error(raw, column=column), worker_id=worker_id)
    try:
        return model(**raw), None
    except ValidationError as exc:
        return None, _log_and_return(_field_error(exc, column=column), worker_id=worker_id)


def _log_and_return(error: WorkerSnapshotError, *, worker_id: str) -> WorkerSnapshotError:
    """响应体是给页面看的，这条日志是给运维看的；两边都得指得出哪台、坏成什么样。"""
    logger.warning(
        "Worker 自报快照读回失败，该列已置空并随响应返回: worker_id={} column={} reason={} keys={} detail={}",
        worker_id,
        error.column,
        error.reason.value,
        error.keys,
        error.message,
    )
    return error


def _structure_error(raw: Any, *, column: str) -> WorkerSnapshotError:
    """整列不是 JSON 对象：没有"哪个键"可指，只能说清实际存的是什么类型。

    不把 ``raw`` 本身放进 message —— 它可能很大，也可能带着不该进响应体的内容。
    """
    return WorkerSnapshotError(
        column=column,
        reason=SnapshotErrorReason.NOT_AN_OBJECT,
        keys=[],
        message=f"该列必须是 JSON 对象，实际存的是 {type(raw).__name__}",
    )


def _field_error(exc: ValidationError, *, column: str) -> WorkerSnapshotError:
    details = {_error_location(error): str(error.get("msg", "")) for error in exc.errors()}
    keys = sorted(details)
    return WorkerSnapshotError(
        column=column,
        reason=SnapshotErrorReason.FIELD_MISMATCH,
        keys=keys,
        message="; ".join(f"{key}: {details[key]}" for key in keys),
    )


def _error_location(error: Any) -> str:
    return ".".join(str(part) for part in error.get("loc", ())) or "<root>"


__all__ = ["WorkerSnapshotReadback", "read_worker_snapshot"]
