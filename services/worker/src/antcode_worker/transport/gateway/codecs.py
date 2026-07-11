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

import json
import re
from datetime import datetime
from typing import Any

from antcode_contracts.transcode import (
    datetime_to_proto_timestamp,
    encode_task_status,
    log_type_str_to_proto,
    proto_timestamp_to_datetime,
)
from loguru import logger

from antcode_worker.domain.models import SourceBundle
from antcode_worker.transport.base import (
    HeartbeatMessage,
    LogMessage,
    TaskMessage,
    TaskResult,
)

# P1: 之前 codecs.py 自己维护了一份字符串-枚举映射 + Timestamp 双向转换，
# 与 redis/transport.py 完全重复。现在统一从 ``antcode_contracts.transcode``
# 导入,见包内 transcode.py 注释中的别名收敛清单。


def _maybe_json(value: Any) -> Any:
    """P1-24 对称解码：还原 gateway ``_pb_scalar_for`` 用 ``json.dumps``
    编码进 ``TaskDispatch.params``（``map<string,string>``）的嵌套结构。

    只还原以 ``{`` / ``[`` 开头且能成功 parse 的 JSON object/array ——
    与 direct 模式（``redis/transport.py::_decode_data`` 对 params 整体
    ``json.loads``）拿到的嵌套 dict/list 对齐。

    bool/int/float 被 ``_pb_scalar_for`` stringify 成 ``"true"`` / ``"123"``
    后，与「原本就是字符串」不可区分（``_pb_scalar_for`` 对 str 透传），
    还原会破坏合法字符串参数，因此一律保持字符串，由消费方按需 coerce。
    现有消费方（``engine._build_payload`` 的 args/kwargs/artifact_patterns
    isinstance 检查、``RulePlugin._extract_rule``）只依赖 dict/list 的还原。
    """
    if isinstance(value, str) and value[:1] in ("{", "["):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


# ---------------------------------------------------------------------------
# TaskDispatch → TaskMessage
# ---------------------------------------------------------------------------
class TaskDecoder:
    """``data_pb2.TaskDispatch`` → ``TaskMessage``。"""

    @staticmethod
    def decode(dispatch: Any) -> TaskMessage:
        try:
            # P1-24: params 值可能是 gateway 端 json.dumps 过的嵌套结构
            # （如 rule 任务的 params["kwargs"] = '{"rule_detail": {...}}'），
            # 必须还原成 dict/list，否则 engine._build_payload 的
            # isinstance(dict/list) 检查全部落空，RulePlugin 拿不到 rule_detail。
            params = {
                str(key): _maybe_json(value)
                for key, value in dict(getattr(dispatch, "params", {}) or {}).items()
            }
            # environment 刻意**不**做 _maybe_json：env 值最终要进子进程
            # environ，必须是字符串；且 JSON 字符串形态的 env 值（如内嵌
            # 配置）在 direct 模式下同样以字符串到达，这里解码会造成两种
            # 传输模式行为分叉。
            environment = dict(getattr(dispatch, "environment", {}) or {})

            # 大对象走 source_bundle；engine._prepare_workspace 依赖
            # task_msg.source_bundle 触发 project_fetcher。缺失 → workspace_path
            # 空 → CodePlugin 找不到 project_cwd 直接抛 ValueError。
            project_type = getattr(dispatch, "project_type", "") or "code"
            source_bundle = TaskDecoder._decode_source_bundle(dispatch, project_type)

            return TaskMessage(
                task_id=getattr(dispatch, "task_id", "") or "",
                project_id=getattr(dispatch, "project_id", "") or "",
                project_type=project_type,
                priority=int(getattr(dispatch, "priority", 0) or 0),
                params=params,
                environment=environment,
                # 新 proto 字段名是 timeout_seconds
                timeout=int(getattr(dispatch, "timeout_seconds", 0) or 3600),
                source_bundle=source_bundle,
                source_subdir=getattr(dispatch, "source_subdir", "") or "",
                entry_point=getattr(dispatch, "entry_point", "") or "",
                run_id=getattr(dispatch, "run_id", "") or "",
                receipt=getattr(dispatch, "receipt_id", "") or None,
            )
        except Exception as e:
            logger.exception("解码任务消息失败")
            raise CodecError(f"解码任务消息失败: {e}") from e

    @staticmethod
    def _decode_source_bundle(dispatch: Any, project_type: str = "") -> SourceBundle | None:
        uri = getattr(dispatch, "source_bundle_uri", "") or ""
        digest = getattr(dispatch, "source_bundle_sha256", "") or ""
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
            size=int(getattr(dispatch, "source_bundle_size", 0) or 0),
            transfer_method=(getattr(dispatch, "transfer_method", "") or "source_bundle"),
            entry_point=getattr(dispatch, "entry_point", "") or "",
            resolved_revision=getattr(dispatch, "resolved_revision", "") or "",
            source_subdir=getattr(dispatch, "source_subdir", "") or "",
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
            exit_code=int(result.exit_code or 0),
            error_message=result.error_message or "",
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
        from antcode_contracts import data_pb2

        entry = data_pb2.LogEntry(
            run_id=log.run_id or "",
            log_type=log_type_str_to_proto(log.log_type),
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
