"""
Gateway Protobuf 编解码模块（P1b 重写）

负责内部数据结构与 ControlService / DataService Proto 消息之间转换。

新协议不再使用 ``gateway_pb2``，由 ControlService + DataService 取代：
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

import json
import re
from collections.abc import Mapping
from typing import Any

from antcode_contracts.transcode import (
    datetime_to_proto_timestamp,
    encode_capabilities,
    encode_task_status,
    proto_timestamp_to_datetime,
)
from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.common.security.task_payload_envelope import open_ready_payload
from loguru import logger

from antcode_worker.domain.models import SourceBundle
from antcode_worker.transport.base import (
    HeartbeatMessage,
    LogMessage,
    TaskMessage,
    TaskResult,
)
from antcode_worker.transport.gateway.heartbeat_metrics import (
    build_metrics_proto,
    heartbeat_metrics_dict,
)
from antcode_worker.transport.task_message_validation import validate_task_message_payload


def _open_gateway_payload(dispatch: Any, *, worker_id: str, worker_secret: str) -> dict[str, Any]:
    if getattr(dispatch, "params", None) or getattr(dispatch, "environment", None):
        raise ValueError("Gateway TaskDispatch 禁止携带明文 params/environment")
    raw = getattr(dispatch, "sealed_ready_payload", None)
    if not isinstance(raw, bytes) or not raw:
        raise ValueError("Gateway TaskDispatch 缺少 sealed_ready_payload")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Gateway sealed_ready_payload 不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Gateway sealed_ready_payload 必须是对象")
    restored = open_ready_payload(payload, worker_id=worker_id, worker_secret=worker_secret)
    if getattr(dispatch, "task_id", "") != restored.get("task_id"):
        raise ValueError("Gateway TaskDispatch task_id 与密文绑定不一致")
    return restored


# ---------------------------------------------------------------------------
# TaskDispatch → TaskMessage
# ---------------------------------------------------------------------------
class TaskDecoder:
    """``data_pb2.TaskDispatch`` → ``TaskMessage``。"""

    @staticmethod
    def decode(dispatch: Any, *, worker_id: str, worker_secret: str) -> TaskMessage:
        try:
            payload = _open_gateway_payload(
                dispatch,
                worker_id=worker_id,
                worker_secret=worker_secret,
            )
            fields = validate_task_message_payload(payload, allow_integer_strings=True)
            source_bundle = TaskDecoder._decode_source_bundle(payload, fields.project_type)

            return TaskMessage(
                task_id=fields.task_id,
                project_id=fields.project_id,
                project_type=fields.project_type,
                priority=fields.priority,
                params=fields.params,
                environment=fields.environment,
                timeout=fields.timeout,
                source_bundle=source_bundle,
                source_subdir=fields.source_subdir,
                entry_point=fields.entry_point,
                runtime_env_name=fields.runtime_env_name,
                run_id=fields.run_id,
                receipt=getattr(dispatch, "receipt_id", "") or None,
            )
        except Exception as e:
            logger.exception("解码任务消息失败")
            raise CodecError(f"解码任务消息失败: {e}") from e

    @staticmethod
    def _decode_source_bundle(payload: Mapping[str, Any], project_type: str = "") -> SourceBundle | None:
        uri = payload.get("source_bundle_uri", "") or ""
        digest = payload.get("source_bundle_sha256", "") or ""
        if not uri and str(project_type).lower() == "rule":
            # rule 任务没有源码 bundle：master 派发时按 project_type 跳过
            # bundle 构建（worker_dispatcher.dispatch_batch O1-followup），
            # source_bundle_uri 恒为空。返回 None，engine._build_payload
            # 对 rule 允许 None bundle；非 rule 任务缺 URI 仍走下面的
            # fail-fast 校验，安全语义不变。
            return None
        match = re.fullmatch(r"pgartifact://([0-9a-f]{64})", uri)
        if match is None:
            raise CodecError("source_bundle_uri 必须是 pgartifact://<sha256>")
        if digest != match.group(1):
            raise CodecError("source_bundle_sha256 与 URI 摘要不一致")
        return SourceBundle(
            uri=uri,
            sha256=digest,
            size=int(payload.get("source_bundle_size", 0) or 0),
            transfer_method=(payload.get("transfer_method", "") or "source_bundle"),
            entry_point=str(payload.get("entry_point", "") or ""),
            resolved_revision=str(payload.get("resolved_revision", "") or ""),
            source_subdir=str(payload.get("source_subdir", "") or ""),
        )


# ---------------------------------------------------------------------------
# TaskResult → TaskStatus
# ---------------------------------------------------------------------------
class TaskStatusEncoder:
    """``TaskResult`` → ``data_pb2.TaskStatus``（StreamStatus 消息体）。

    实际工作委托给 ``antcode_contracts.transcode.encode_task_status``，
    保持与 Direct 模式同一份 Status 别名映射 / data map 处理逻辑。
    """

    @staticmethod
    def encode(result: TaskResult, worker_id: str) -> Any:
        return encode_task_status(
            run_id=result.run_id or "",
            task_id=result.task_id or "",
            worker_id=worker_id or "",
            status=result.status,
            exit_code=result.exit_code,
            error_message=normalize_persisted_error_message(result.error_message) or "",
            started_at=result.started_at,
            finished_at=result.finished_at,
            duration_ms=int(result.duration_ms or 0),
            data=result.data,
        )


# ---------------------------------------------------------------------------
# LogMessage → LogEntry/LogBatch
# ---------------------------------------------------------------------------
class LogEncoder:
    """``LogMessage`` 列表 → ``data_pb2.LogBatch``（StreamLogs 消息体）。"""

    @staticmethod
    def encode_entry(log: LogMessage) -> Any:
        # 唯一实现在 transport.log_batches（Gateway/Direct 共用）。
        from antcode_worker.transport.log_batches import encode_log_entry

        return encode_log_entry(log)

    @staticmethod
    def encode_batch(
        logs: list[LogMessage],
        worker_id: str = "",
        lease_id: str = "",
    ) -> Any:
        from antcode_contracts import data_pb2

        batch = data_pb2.LogBatch(
            worker_id=worker_id or "",
            lease_id=lease_id or "",
        )
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
        from antcode_contracts import control_pb2

        metrics = build_metrics_proto(heartbeat_metrics_dict(heartbeat))

        return control_pb2.LeaseRequest(
            worker_id=worker_id or "",
            current_lease_id=current_lease_id or "",
            metrics=metrics,
            capabilities=encode_capabilities(getattr(heartbeat, "capabilities", None)),
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
          - ``action``: 路由用的 action 名（**主字段**，引擎按它查表）
          - ``params``: ``map<string,string>``，顶层参数（如 reply_stream）
          - ``action_typed``: ``RuntimeAction`` oneof；当前只填充 ``generic``
            分支，``generic.name`` 与 ``action`` 同源、仅在 ``action`` 为空时兜底
            回填，``generic.args`` 是真正的任务参数（与旧 ``payload`` 等价）。

        引擎层（``_handle_runtime_control``）继续按 ``action`` 路由 + 按字段名
        从 ``args`` 取值；不再用 ``payload_json`` 和 ``reply_stream`` 字段。
        """
        request_id = getattr(runtime, "request_id", "") or ""
        action = getattr(runtime, "action", "") or ""
        expires_at_ms = int(getattr(runtime, "expires_at_ms", 0) or 0)
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
            "expires_at_ms": expires_at_ms,
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
