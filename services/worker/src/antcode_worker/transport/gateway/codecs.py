"""
Gateway Protobuf 编解码模块（P1b 重写）

负责在 ``antcode_worker.transport.base`` 的内部数据结构与新的
``antcode_contracts.control_pb2`` / ``data_pb2`` Proto 消息之间转换。

新协议要点：
- 不再使用 ``gateway_pb2``（已被 ControlService + DataService 取代）。
- 任务投递走 ``DataService.StreamTasks``（server-stream），消息体是
  ``data_pb2.TaskDispatch``。
- 任务状态走 ``DataService.StreamStatus``（client-stream），消息体是
  ``data_pb2.TaskStatus``，状态枚举 ``data_pb2.Status``。
- 日志走 ``DataService.StreamLogs``（client-stream），消息体是
  ``data_pb2.LogBatch``（含 1..N 条 ``LogEntry``）。
- 控制事件走 ``ControlService.WatchControl``（server-stream），消息体是
  ``control_pb2.ControlEvent``，``payload`` 是 oneof
  （``task_cancel`` / ``config_update`` / ``runtime_control`` / ``ping``）。
- ``RuntimeControl`` 不再有 ``payload_json`` / ``reply_stream`` 字段，全部
  改为 typed ``params: map<string,string>`` + ``action_typed`` oneof。

Requirements: 5.5
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger

from antcode_worker.transport.base import (
    HeartbeatMessage,
    LogMessage,
    TaskMessage,
    TaskResult,
)


# ---------------------------------------------------------------------------
# 字符串 <-> Proto enum 映射（与 Direct transport 共享同一约定）
# ---------------------------------------------------------------------------
def _status_str_to_proto(status: str) -> int:
    from antcode_contracts import data_pb2

    mapping = {
        "": data_pb2.Status.STATUS_UNSPECIFIED,
        "pending": data_pb2.Status.STATUS_PENDING,
        "running": data_pb2.Status.STATUS_RUNNING,
        "success": data_pb2.Status.STATUS_COMPLETED,
        "completed": data_pb2.Status.STATUS_COMPLETED,
        "done": data_pb2.Status.STATUS_COMPLETED,
        "failed": data_pb2.Status.STATUS_FAILED,
        "failure": data_pb2.Status.STATUS_FAILED,
        "error": data_pb2.Status.STATUS_FAILED,
        "cancelled": data_pb2.Status.STATUS_CANCELLED,
        "canceled": data_pb2.Status.STATUS_CANCELLED,
        "timeout": data_pb2.Status.STATUS_TIMEOUT,
        "timed_out": data_pb2.Status.STATUS_TIMEOUT,
    }
    return mapping.get((status or "").lower(), data_pb2.Status.STATUS_UNSPECIFIED)


def _log_type_str_to_proto(log_type: str) -> int:
    from antcode_contracts import data_pb2

    mapping = {
        "": data_pb2.LogType.LOG_TYPE_UNSPECIFIED,
        "stdout": data_pb2.LogType.LOG_TYPE_STDOUT,
        "stderr": data_pb2.LogType.LOG_TYPE_STDERR,
        "system": data_pb2.LogType.LOG_TYPE_SYSTEM,
    }
    return mapping.get((log_type or "").lower(), data_pb2.LogType.LOG_TYPE_UNSPECIFIED)


def datetime_to_proto_timestamp(dt: datetime | None) -> Any:
    """``datetime`` → ``common_pb2.Timestamp``，``None`` 时返回 ``None``。"""
    if dt is None:
        return None
    from antcode_contracts import common_pb2

    ts = common_pb2.Timestamp()
    epoch = dt.timestamp()
    ts.seconds = int(epoch)
    ts.nanos = int((epoch - int(epoch)) * 1e9)
    return ts


def proto_timestamp_to_datetime(ts: Any) -> datetime | None:
    """``common_pb2.Timestamp`` → ``datetime``，未设置时返回 ``None``。"""
    if ts is None:
        return None
    seconds = getattr(ts, "seconds", 0)
    nanos = getattr(ts, "nanos", 0)
    if seconds == 0 and nanos == 0:
        return None
    return datetime.fromtimestamp(seconds + nanos / 1e9)


# ---------------------------------------------------------------------------
# TaskDispatch → TaskMessage
# ---------------------------------------------------------------------------
class TaskDecoder:
    """``data_pb2.TaskDispatch`` → ``TaskMessage``。"""

    @staticmethod
    def decode(dispatch: Any) -> TaskMessage:
        try:
            params = dict(getattr(dispatch, "params", {}) or {})
            environment = dict(getattr(dispatch, "environment", {}) or {})

            return TaskMessage(
                task_id=getattr(dispatch, "task_id", "") or "",
                project_id=getattr(dispatch, "project_id", "") or "",
                project_type=getattr(dispatch, "project_type", "") or "code",
                priority=int(getattr(dispatch, "priority", 0) or 0),
                params=params,
                environment=environment,
                # 新 proto 字段名是 timeout_seconds
                timeout=int(getattr(dispatch, "timeout_seconds", 0) or 3600),
                # 大对象走 source_bundle，旧的 download_url 留空
                download_url=getattr(dispatch, "source_bundle_uri", "") or "",
                file_hash=getattr(dispatch, "source_bundle_sha256", "") or "",
                entry_point=getattr(dispatch, "entry_point", "") or "",
                run_id=getattr(dispatch, "run_id", "") or "",
                receipt=getattr(dispatch, "receipt_id", "") or None,
            )
        except Exception as e:
            logger.error(f"解码任务消息失败: {e}")
            raise CodecError(f"解码任务消息失败: {e}") from e


# ---------------------------------------------------------------------------
# TaskResult → TaskStatus
# ---------------------------------------------------------------------------
class TaskStatusEncoder:
    """``TaskResult`` → ``data_pb2.TaskStatus``（StreamStatus 消息体）。"""

    @staticmethod
    def encode(result: TaskResult, worker_id: str) -> Any:
        from antcode_contracts import data_pb2

        msg = data_pb2.TaskStatus(
            run_id=result.run_id or "",
            task_id=result.task_id or "",
            worker_id=worker_id or "",
            status=_status_str_to_proto(result.status),
            exit_code=int(result.exit_code or 0),
            error_message=result.error_message or "",
            duration_ms=int(result.duration_ms or 0),
        )
        started = datetime_to_proto_timestamp(result.started_at)
        if started is not None:
            msg.started_at.CopyFrom(started)
        finished = datetime_to_proto_timestamp(result.finished_at)
        if finished is not None:
            msg.finished_at.CopyFrom(finished)
        if result.data:
            for k, v in result.data.items():
                key = str(k)
                if isinstance(v, (str, int, float, bool)):
                    msg.data[key] = str(v)
                else:
                    import json

                    try:
                        msg.data[key] = json.dumps(v, ensure_ascii=False)
                    except Exception:
                        msg.data[key] = repr(v)
        return msg


# ---------------------------------------------------------------------------
# LogMessage → LogEntry/LogBatch
# ---------------------------------------------------------------------------
class LogEncoder:
    """``LogMessage`` 列表 → ``data_pb2.LogBatch``（StreamLogs 消息体）。"""

    @staticmethod
    def encode_entry(log: LogMessage) -> Any:
        from antcode_contracts import data_pb2

        entry = data_pb2.LogEntry(
            run_id=log.run_id or "",
            log_type=_log_type_str_to_proto(log.log_type),
            content=log.content or "",
            sequence=int(log.sequence or 0),
        )
        ts = datetime_to_proto_timestamp(log.timestamp or datetime.now())
        if ts is not None:
            entry.timestamp.CopyFrom(ts)
        return entry

    @staticmethod
    def encode_batch(logs: list[LogMessage], worker_id: str = "") -> Any:
        from antcode_contracts import data_pb2

        batch = data_pb2.LogBatch(worker_id=worker_id or "")
        for log in logs:
            batch.entries.append(LogEncoder.encode_entry(log))
        return batch


# ---------------------------------------------------------------------------
# Heartbeat → Lease（控制面）
# ---------------------------------------------------------------------------
class HeartbeatEncoder:
    """``HeartbeatMessage`` → ``control_pb2.LeaseRequest``。

    新协议把心跳重命名为 Lease（liveness signal），Metrics 直接挂在同一个
    请求上。Worker 内部仍叫 ``send_heartbeat``，Gateway transport 把它桥到
    ``ControlService.Lease``。
    """

    @staticmethod
    def encode_lease(
        heartbeat: HeartbeatMessage,
        worker_id: str,
        current_lease_id: str = "",
    ) -> Any:
        from antcode_contracts import common_pb2, control_pb2

        metrics_obj = getattr(heartbeat, "metrics", None)
        if metrics_obj is not None:
            cpu = float(getattr(metrics_obj, "cpu", 0.0) or 0.0)
            memory = float(getattr(metrics_obj, "memory", 0.0) or 0.0)
            disk = float(getattr(metrics_obj, "disk", 0.0) or 0.0)
            running_tasks = int(getattr(metrics_obj, "running_tasks", 0) or 0)
            max_concurrent = int(getattr(metrics_obj, "max_concurrent_tasks", 0) or 0)
        else:
            cpu = float(getattr(heartbeat, "cpu_percent", 0.0) or 0.0)
            memory = float(getattr(heartbeat, "memory_percent", 0.0) or 0.0)
            disk = float(getattr(heartbeat, "disk_percent", 0.0) or 0.0)
            running_tasks = int(getattr(heartbeat, "running_tasks", 0) or 0)
            max_concurrent = int(getattr(heartbeat, "max_concurrent_tasks", 0) or 0)

        metrics = common_pb2.Metrics(
            cpu=cpu,
            memory=memory,
            disk=disk,
            running_tasks=running_tasks,
            max_concurrent_tasks=max_concurrent,
        )

        return control_pb2.LeaseRequest(
            worker_id=worker_id or "",
            current_lease_id=current_lease_id or "",
            metrics=metrics,
        )


# ---------------------------------------------------------------------------
# ControlEvent → 内部 ControlMessage 字段抽取
# ---------------------------------------------------------------------------
class ControlDecoder:
    """``control_pb2.ControlEvent.payload`` 各 oneof 分支的字段提取。

    返回普通字典，由 ``GatewayTransport`` 包装成 ``ControlMessage``。
    """

    @staticmethod
    def decode_task_cancel(task_cancel: Any) -> dict[str, Any]:
        return {
            "task_id": getattr(task_cancel, "task_id", "") or "",
            "run_id": getattr(task_cancel, "run_id", "") or "",
            "reason": getattr(task_cancel, "reason", "") or "",
        }

    @staticmethod
    def decode_config_update(config_update: Any) -> dict[str, str]:
        cfg = getattr(config_update, "config", None)
        return dict(cfg) if cfg else {}

    @staticmethod
    def decode_runtime_control(runtime: Any) -> dict[str, Any]:
        """``control_pb2.RuntimeControl`` → 扁平 dict。

        新协议下 ``RuntimeControl``:
          - ``request_id``: correlation id（必填，用于 AckControl 回报）
          - ``action``: 路由用的 action 名（legacy，保留兼容）
          - ``params``: ``map<string,string>``，顶层参数（如 reply_stream）
          - ``action_typed``: ``RuntimeAction`` oneof（首选）；当前只填充
            ``generic`` 分支，``generic.name`` 与 ``action`` 同源，
            ``generic.args`` 是真正的任务参数（与旧 ``payload`` 等价）。

        引擎层（``_handle_runtime_control``）继续按 ``action`` 路由 + 按字段名
        从 ``args`` 取值；不再用 ``payload_json`` 和 ``reply_stream`` 字段。
        """
        request_id = getattr(runtime, "request_id", "") or ""
        action = getattr(runtime, "action", "") or ""
        params = dict(getattr(runtime, "params", {}) or {})

        args: dict[str, str] = {}
        action_typed = getattr(runtime, "action_typed", None)
        if action_typed is not None:
            which = None
            try:
                which = action_typed.WhichOneof("payload")
            except Exception:
                which = None
            if which == "generic":
                generic = action_typed.generic
                generic_name = getattr(generic, "name", "") or ""
                if generic_name and not action:
                    action = generic_name
                generic_args = getattr(generic, "args", None)
                if generic_args:
                    args.update(dict(generic_args))

        return {
            "request_id": request_id,
            "action": action,
            "params": params,
            "args": args,
        }

    @staticmethod
    def decode_ping(ping: Any) -> dict[str, Any]:
        ts = getattr(ping, "timestamp", None)
        return {"timestamp": proto_timestamp_to_datetime(ts)}


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------
class CodecError(Exception):
    """编解码错误"""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


__all__ = [
    "TaskDecoder",
    "TaskStatusEncoder",
    "LogEncoder",
    "HeartbeatEncoder",
    "ControlDecoder",
    "CodecError",
    "datetime_to_proto_timestamp",
    "proto_timestamp_to_datetime",
]
