"""统一 Proto <-> Python 类型转换工具

收敛原先分散在 ``services/worker/transport/redis/transport.py`` 与
``services/worker/transport/gateway/codecs.py`` 中的多份重复实现
(``_STATUS_STR_TO_PROTO`` / ``_status_str_to_proto`` /
``_log_type_str_to_proto`` / ``_datetime_to_proto_timestamp`` 等)。

Master 端 ``services/master/.../ingester/result_loop.py`` 与
``log_ingest_loop.py`` 中的反向映射保持自己实现（短期内不动），
本模块对外同样导出 ``proto_status_to_str`` / ``proto_log_type_to_str`` /
``proto_timestamp_to_datetime`` / ``task_status_to_dict``，
以便后续 Master 侧切换到本模块。

设计目标：
* 单一真相来源：枚举别名映射（``success`` / ``done`` / ``completed`` ...
  全部归并到 ``STATUS_COMPLETED``）。
* 双向：既能字符串/datetime → Proto，也能 Proto → str/datetime。
* 高层组合：``encode_task_status`` 接收常用 kwargs 直接产出
  ``data_pb2.TaskStatus``，避免每个调用点都重复几十行 set 字段。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from antcode_contracts import common_pb2, data_pb2
from antcode_contracts.capabilities import decode_capabilities, encode_capabilities, validate_capabilities

# ---------------------------------------------------------------------------
# Status 别名映射
# ---------------------------------------------------------------------------
#
# 多端共用的字符串别名集合：
#   * ``success`` / ``done`` / ``completed`` → ``STATUS_COMPLETED``
#   * ``failure`` / ``error`` / ``failed``    → ``STATUS_FAILED``
#   * ``canceled`` / ``cancelled``            → ``STATUS_CANCELLED``
#   * ``timed_out`` / ``timeout``             → ``STATUS_TIMEOUT``
#
# 同一别名集出现在历史的多个文件中（worker direct / worker gateway /
# master ingester）。本模块作为唯一来源，避免三份独立列表漂移。
_STATUS_STR_TO_PROTO: dict[str, data_pb2.Status] = {
    "": data_pb2.Status.STATUS_UNSPECIFIED,
    "pending": data_pb2.Status.STATUS_PENDING,
    "preparing": data_pb2.Status.STATUS_PENDING,  # E2: worker 内部准备阶段视为 PENDING
    "running": data_pb2.Status.STATUS_RUNNING,
    "success": data_pb2.Status.STATUS_COMPLETED,
    "completed": data_pb2.Status.STATUS_COMPLETED,
    "done": data_pb2.Status.STATUS_COMPLETED,
    "failed": data_pb2.Status.STATUS_FAILED,
    "failure": data_pb2.Status.STATUS_FAILED,
    "error": data_pb2.Status.STATUS_FAILED,
    "killed": data_pb2.Status.STATUS_FAILED,  # E2: 被 kill 视为失败（proto 无 KILLED）
    "cancelled": data_pb2.Status.STATUS_CANCELLED,
    "canceled": data_pb2.Status.STATUS_CANCELLED,
    "timeout": data_pb2.Status.STATUS_TIMEOUT,
    "timed_out": data_pb2.Status.STATUS_TIMEOUT,
}


_PROTO_STATUS_TO_CANONICAL: dict[int, str] = {
    data_pb2.Status.STATUS_UNSPECIFIED: "",
    data_pb2.Status.STATUS_PENDING: "pending",
    data_pb2.Status.STATUS_RUNNING: "running",
    data_pb2.Status.STATUS_COMPLETED: "completed",
    data_pb2.Status.STATUS_FAILED: "failed",
    data_pb2.Status.STATUS_CANCELLED: "cancelled",
    data_pb2.Status.STATUS_TIMEOUT: "timeout",
}


def status_str_to_proto(status: str | None) -> data_pb2.Status:
    """字符串 status → Proto enum int。

    ``None`` / 空字符串 / 未知值 → ``STATUS_UNSPECIFIED``。
    """
    if not status:
        return data_pb2.Status.STATUS_UNSPECIFIED
    return _STATUS_STR_TO_PROTO.get(status.lower(), data_pb2.Status.STATUS_UNSPECIFIED)


def proto_status_to_str(status: int) -> str:
    """Proto enum int → 规范字符串。未知值返回 ``""``。"""
    return _PROTO_STATUS_TO_CANONICAL.get(int(status), "")


# ---------------------------------------------------------------------------
# LogType 映射
# ---------------------------------------------------------------------------
_LOG_TYPE_STR_TO_PROTO: dict[str, data_pb2.LogType] = {
    "": data_pb2.LogType.LOG_TYPE_UNSPECIFIED,
    "stdout": data_pb2.LogType.LOG_TYPE_STDOUT,
    "stderr": data_pb2.LogType.LOG_TYPE_STDERR,
    "system": data_pb2.LogType.LOG_TYPE_SYSTEM,
}


_PROTO_LOG_TYPE_TO_STR: dict[int, str] = {
    data_pb2.LogType.LOG_TYPE_UNSPECIFIED: "",
    data_pb2.LogType.LOG_TYPE_STDOUT: "stdout",
    data_pb2.LogType.LOG_TYPE_STDERR: "stderr",
    data_pb2.LogType.LOG_TYPE_SYSTEM: "system",
}


def log_type_str_to_proto(log_type: str | None) -> data_pb2.LogType:
    """字符串 log_type → Proto enum int。

    ``None`` / 空字符串 / 未知值 → ``LOG_TYPE_UNSPECIFIED``。
    """
    if not log_type:
        return data_pb2.LogType.LOG_TYPE_UNSPECIFIED
    return _LOG_TYPE_STR_TO_PROTO.get(log_type.lower(), data_pb2.LogType.LOG_TYPE_UNSPECIFIED)


def proto_log_type_to_str(log_type: int) -> str:
    """Proto enum int → 规范字符串。未知值返回 ``""``。"""
    return _PROTO_LOG_TYPE_TO_STR.get(int(log_type), "")


# ---------------------------------------------------------------------------
# Timestamp 双向
# ---------------------------------------------------------------------------
def datetime_to_proto_timestamp(dt: datetime | None) -> common_pb2.Timestamp | None:
    """``datetime`` → ``common_pb2.Timestamp``。``None`` → ``None``。

    naive ``datetime`` 按本地时区解释（``datetime.timestamp()`` 默认行为）。
    精度按 ``datetime.microsecond`` 直接计算，避免 ``epoch * 1e9`` 的浮点
    舍入（之前两份本地实现都是 ``int((epoch - int(epoch)) * 1e9)`` 形态，
    在大时间戳上会漂几纳秒）。
    """
    if dt is None:
        return None
    ts = common_pb2.Timestamp()
    # 用整秒 + 微秒分别取，避免浮点；和 datetime.timestamp() 一致是因为
    # datetime.timestamp() 内部也是把 microsecond 直接加到 seconds 上。
    epoch_seconds = int(dt.timestamp()) if dt.microsecond == 0 else int(dt.replace(microsecond=0).timestamp())
    ts.seconds = epoch_seconds
    ts.nanos = dt.microsecond * 1000
    return ts


def proto_timestamp_to_datetime(
    ts: common_pb2.Timestamp | None,
) -> datetime | None:
    """``common_pb2.Timestamp`` → ``datetime``。

    全零 / ``None`` → ``None``，与上游过去的语义一致。
    """
    if ts is None:
        return None
    seconds = int(getattr(ts, "seconds", 0) or 0)
    nanos = int(getattr(ts, "nanos", 0) or 0)
    if seconds == 0 and nanos == 0:
        return None
    return datetime.fromtimestamp(seconds + nanos / 1e9)


# ---------------------------------------------------------------------------
# 高层组合：TaskStatus
# ---------------------------------------------------------------------------
def encode_task_status(
    *,
    run_id: str,
    task_id: str,
    worker_id: str,
    status: str,
    exit_code: int | None = None,
    error_message: str = "",
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    duration_ms: int = 0,
    data: dict[str, Any] | None = None,
) -> data_pb2.TaskStatus:
    """构造 ``data_pb2.TaskStatus``。

    * ``status`` 接受字符串别名（``success`` / ``failed`` / ...）。
    * ``exit_code=None`` = 没有进程退出码（还在跑，或压根没跑起来），字段缺席；
      ``0`` 是"进程真的退出 0"，两者不可互换。
    * ``data`` 是 ``map<string,string>``：值非 ``(str|int|float|bool)``
      时会用 ``json.dumps`` fallback。
    * 不在此处注入 trace —— trace 由调用方在出站点 ``inject_trace`` 显式
      绑定（与 ``send_log_batch`` / ``report_result`` 的现有约定一致）。
    """
    msg = data_pb2.TaskStatus(
        run_id=run_id or "",
        task_id=task_id or "",
        worker_id=worker_id or "",
        status=status_str_to_proto(status),
        error_message=error_message or "",
        duration_ms=int(duration_ms or 0),
    )
    if exit_code is not None:
        msg.exit_code = int(exit_code)
    started_ts = datetime_to_proto_timestamp(started_at)
    if started_ts is not None:
        msg.started_at.CopyFrom(started_ts)
    finished_ts = datetime_to_proto_timestamp(finished_at)
    if finished_ts is not None:
        msg.finished_at.CopyFrom(finished_ts)

    if data:
        for k, v in data.items():
            key = str(k)
            if isinstance(v, (str, int, float, bool)):
                msg.data[key] = str(v)
            else:
                try:
                    msg.data[key] = json.dumps(v, ensure_ascii=False)
                except Exception:
                    msg.data[key] = repr(v)
    return msg


def task_status_to_dict(ts: data_pb2.TaskStatus) -> dict[str, Any]:
    """反向：``TaskStatus`` Proto → 通用 dict，供 Master ingester 使用。

    与 ``encode_task_status`` 关键字参数同形（``status`` 是规范字符串、
    ``started_at`` / ``finished_at`` 是 ``datetime | None``）。
    """
    return {
        "run_id": ts.run_id or "",
        "task_id": ts.task_id or "",
        "worker_id": ts.worker_id or "",
        "status": proto_status_to_str(ts.status),
        "exit_code": int(ts.exit_code) if ts.HasField("exit_code") else None,
        "error_message": ts.error_message or "",
        "started_at": proto_timestamp_to_datetime(ts.started_at if ts.HasField("started_at") else None),
        "finished_at": proto_timestamp_to_datetime(ts.finished_at if ts.HasField("finished_at") else None),
        "duration_ms": int(ts.duration_ms or 0),
        "data": dict(ts.data) if ts.data else {},
    }


__all__ = [
    # Status
    "status_str_to_proto",
    "proto_status_to_str",
    # LogType
    "log_type_str_to_proto",
    "proto_log_type_to_str",
    # Timestamp
    "datetime_to_proto_timestamp",
    "proto_timestamp_to_datetime",
    # 高层组合
    "encode_task_status",
    "task_status_to_dict",
    "validate_capabilities",
    "encode_capabilities",
    "decode_capabilities",
]
