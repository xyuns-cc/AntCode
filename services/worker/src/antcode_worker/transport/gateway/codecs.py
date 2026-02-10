"""
Gateway Protobuf 编解码模块

实现 protobuf 消息的编解码，支持 schema versioning。

Requirements: 5.5
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from loguru import logger

from antcode_worker.transport.base import (
    HeartbeatMessage,
    LogMessage,
    TaskMessage,
    TaskResult,
)


class SchemaVersion(str, Enum):
    """Schema 版本"""

    V1 = "v1"
    V2 = "v2"


@dataclass
class CodecConfig:
    """编解码配置"""

    version: SchemaVersion = SchemaVersion.V1
    strict_mode: bool = False  # 严格模式下，未知字段会报错
    default_timeout: int = 3600
    default_priority: int = 0


class GatewayCodec:
    """
    Gateway 编解码器

    提供 protobuf 消息与内部数据结构之间的转换。
    """

    def __init__(self, config: CodecConfig | None = None):
        self._config = config or CodecConfig()

    @property
    def version(self) -> SchemaVersion:
        return self._config.version


class TaskDecoder:
    """
    任务消息解码器

    将 protobuf TaskDispatch 消息解码为 TaskMessage。
    """

    @staticmethod
    def decode(proto_task: Any) -> TaskMessage:
        """
        解码任务消息

        Args:
            proto_task: protobuf TaskDispatch 消息

        Returns:
            TaskMessage 实例
        """
        try:
            # 解析参数
            params = {}
            if hasattr(proto_task, "params") and proto_task.params:
                params = dict(proto_task.params)

            # 解析环境变量
            environment = {}
            if hasattr(proto_task, "environment") and proto_task.environment:
                environment = dict(proto_task.environment)

            return TaskMessage(
                task_id=getattr(proto_task, "task_id", ""),
                project_id=getattr(proto_task, "project_id", ""),
                project_type=getattr(proto_task, "project_type", "code"),
                priority=getattr(proto_task, "priority", 0),
                params=params,
                environment=environment,
                timeout=getattr(proto_task, "timeout", 3600),
                download_url=getattr(proto_task, "download_url", ""),
                file_hash=getattr(proto_task, "file_hash", ""),
                entry_point=getattr(proto_task, "entry_point", ""),
                run_id=getattr(proto_task, "run_id", ""),
            )

        except Exception as e:
            logger.error(f"解码任务消息失败: {e}")
            raise CodecError(f"解码任务消息失败: {e}") from e

    @staticmethod
    def decode_from_dict(data: dict[str, Any]) -> TaskMessage:
        """
        从字典解码任务消息

        Args:
            data: 任务数据字典

        Returns:
            TaskMessage 实例
        """
        return TaskMessage(
            task_id=data.get("task_id", ""),
            project_id=data.get("project_id", ""),
            project_type=data.get("project_type", "code"),
            priority=int(data.get("priority", 0)),
            params=data.get("params", {}),
            environment=data.get("environment", {}),
            timeout=int(data.get("timeout", 3600)),
            download_url=data.get("download_url", ""),
            file_hash=data.get("file_hash", ""),
            entry_point=data.get("entry_point", ""),
            run_id=data.get("run_id", ""),
        )


class ResultEncoder:
    """
    结果消息编码器

    将 TaskResult 编码为 protobuf ReportResultRequest 消息。
    """

    @staticmethod
    def encode(result: TaskResult, worker_id: str) -> Any:
        """
        编码结果消息

        Args:
            result: TaskResult 实例
            worker_id: Worker ID

        Returns:
            protobuf ReportResultRequest 消息
        """
        from antcode_contracts import gateway_pb2

        return gateway_pb2.ReportResultRequest(
            run_id=result.run_id,
            task_id=result.task_id,
            worker_id=worker_id,
            status=result.status,
            exit_code=result.exit_code,
            error_message=result.error_message or "",
            started_at=result.started_at.isoformat() if result.started_at else "",
            finished_at=result.finished_at.isoformat() if result.finished_at else "",
            duration_ms=int(result.duration_ms),
            data_json="" if not result.data else json.dumps(result.data, ensure_ascii=False),
        )

    @staticmethod
    def encode_to_dict(result: TaskResult, worker_id: str) -> dict[str, Any]:
        """
        编码结果为字典

        Args:
            result: TaskResult 实例
            worker_id: Worker ID

        Returns:
            结果数据字典
        """
        return {
            "run_id": result.run_id,
            "task_id": result.task_id,
            "worker_id": worker_id,
            "status": result.status,
            "exit_code": result.exit_code,
            "error_message": result.error_message or "",
            "started_at": result.started_at.isoformat() if result.started_at else "",
            "finished_at": result.finished_at.isoformat() if result.finished_at else "",
            "duration_ms": int(result.duration_ms),
            "data": result.data,
        }


class LogEncoder:
    """
    日志消息编码器

    将 LogMessage 编码为 protobuf 消息。
    """

    @staticmethod
    def encode_realtime(log: LogMessage) -> Any:
        """
        编码实时日志消息

        Args:
            log: LogMessage 实例

        Returns:
            protobuf SendLogRequest 消息
        """
        from antcode_contracts import gateway_pb2

        return gateway_pb2.SendLogRequest(
            run_id=log.run_id,
            log_type=log.log_type,
            content=log.content,
            timestamp=(log.timestamp or datetime.now()).isoformat(),
            sequence=log.sequence,
        )

    @staticmethod
    def encode_chunk(
        run_id: str,
        log_type: str,
        data: bytes,
        offset: int,
        is_final: bool = False,
        checksum: str | None = None,
        total_size: int | None = None,
    ) -> Any:
        """
        编码日志分片消息

        Args:
            run_id: 运行 ID
            log_type: 日志类型
            data: 日志数据
            offset: 偏移量
            is_final: 是否最后一片
            checksum: 校验和
            total_size: 总大小

        Returns:
            protobuf SendLogChunkRequest 消息
        """
        from antcode_contracts import gateway_pb2

        request = gateway_pb2.SendLogChunkRequest(
            run_id=run_id,
            log_type=log_type,
            data=data,
            offset=offset,
            is_final=is_final,
        )

        if checksum:
            request.checksum = checksum
        if total_size is not None:
            request.total_size = total_size

        return request

    @staticmethod
    def encode_batch(logs: list[LogMessage]) -> Any:
        """
        编码批量日志消息

        Args:
            logs: LogMessage 列表

        Returns:
            protobuf SendLogBatchRequest 消息
        """
        from antcode_contracts import gateway_pb2

        log_entries = []
        for log in logs:
            entry = gateway_pb2.LogEntry(
                run_id=log.run_id,
                log_type=log.log_type,
                content=log.content,
                timestamp=(log.timestamp or datetime.now()).isoformat(),
                sequence=log.sequence,
            )
            log_entries.append(entry)

        return gateway_pb2.SendLogBatchRequest(logs=log_entries)

    @staticmethod
    def encode_to_dict(log: LogMessage) -> dict[str, Any]:
        """
        编码日志为字典

        Args:
            log: LogMessage 实例

        Returns:
            日志数据字典
        """
        return {
            "run_id": log.run_id,
            "log_type": log.log_type,
            "content": log.content,
            "timestamp": (log.timestamp or datetime.now()).isoformat(),
            "sequence": log.sequence,
        }


class HeartbeatEncoder:
    """
    心跳消息编码器

    将 HeartbeatMessage 编码为 protobuf 消息。
    """

    @staticmethod
    def encode(heartbeat: HeartbeatMessage) -> Any:
        """
        编码心跳消息

        Args:
            heartbeat: HeartbeatMessage 实例

        Returns:
            protobuf SendHeartbeatRequest 消息
        """
        worker_id = getattr(heartbeat, "worker_id", "")
        status = getattr(heartbeat, "status", "online")
        metrics = getattr(heartbeat, "metrics", None)
        if metrics is not None:
            cpu_percent = getattr(metrics, "cpu", 0.0)
            memory_percent = getattr(metrics, "memory", 0.0)
            disk_percent = getattr(metrics, "disk", 0.0)
            running_tasks = getattr(metrics, "running_tasks", 0)
            max_concurrent_tasks = getattr(metrics, "max_concurrent_tasks", 5)
        else:
            cpu_percent = getattr(heartbeat, "cpu_percent", 0.0)
            memory_percent = getattr(heartbeat, "memory_percent", 0.0)
            disk_percent = getattr(heartbeat, "disk_percent", 0.0)
            running_tasks = getattr(heartbeat, "running_tasks", 0)
            max_concurrent_tasks = getattr(heartbeat, "max_concurrent_tasks", 5)

        version = getattr(heartbeat, "version", "")

        from antcode_contracts import gateway_pb2

        return gateway_pb2.SendHeartbeatRequest(
            worker_id=worker_id,
            status=status,
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            disk_percent=disk_percent,
            running_tasks=running_tasks,
            max_concurrent_tasks=max_concurrent_tasks,
            timestamp=(heartbeat.timestamp or datetime.now()).isoformat(),
            version=version,
        )

    @staticmethod
    def encode_to_dict(heartbeat: HeartbeatMessage) -> dict[str, Any]:
        """
        编码心跳为字典

        Args:
            heartbeat: HeartbeatMessage 实例

        Returns:
            心跳数据字典
        """
        return {
            "worker_id": heartbeat.worker_id,
            "status": heartbeat.status,
            "cpu_percent": heartbeat.cpu_percent,
            "memory_percent": heartbeat.memory_percent,
            "disk_percent": heartbeat.disk_percent,
            "running_tasks": heartbeat.running_tasks,
            "max_concurrent_tasks": heartbeat.max_concurrent_tasks,
            "timestamp": (heartbeat.timestamp or datetime.now()).isoformat(),
        }


class ControlDecoder:
    """
    控制消息解码器

    解码来自 Gateway 的控制消息（取消、配置更新等）。
    """

    @staticmethod
    def decode_cancel(proto_cancel: Any) -> dict[str, Any]:
        """
        解码取消消息

        Args:
            proto_cancel: protobuf TaskCancel 消息

        Returns:
            取消信息字典
        """
        from antcode_core.infrastructure.redis import build_cancel_control_payload

        return build_cancel_control_payload(
            run_id=getattr(proto_cancel, "run_id", ""),
            task_id=getattr(proto_cancel, "task_id", ""),
        )

    @staticmethod
    def decode_config_update(proto_config: Any) -> dict[str, str]:
        """
        解码配置更新消息

        Args:
            proto_config: protobuf ConfigUpdate 消息

        Returns:
            配置字典
        """
        if hasattr(proto_config, "config") and proto_config.config:
            return dict(proto_config.config)
        return {}

    @staticmethod
    def decode_runtime_control(proto_runtime: Any) -> dict[str, Any]:
        """
        解码运行时管理控制消息

        Args:
            proto_runtime: protobuf RuntimeControl 消息

        Returns:
            运行时控制信息
        """
        payload = {}
        if getattr(proto_runtime, "payload_json", ""):
            try:
                payload = json.loads(proto_runtime.payload_json)
            except Exception:
                payload = {}

        return {
            "request_id": getattr(proto_runtime, "request_id", ""),
            "action": getattr(proto_runtime, "action", ""),
            "reply_stream": getattr(proto_runtime, "reply_stream", ""),
            "payload": payload,
        }

    @staticmethod
    def decode_ping(proto_ping: Any) -> dict[str, Any]:
        """
        解码 Ping 消息

        Args:
            proto_ping: protobuf Ping 消息

        Returns:
            Ping 信息字典
        """
        timestamp = None
        if hasattr(proto_ping, "timestamp") and proto_ping.timestamp:
            ts = proto_ping.timestamp
            if hasattr(ts, "seconds"):
                timestamp = datetime.fromtimestamp(ts.seconds + ts.nanos / 1e9)

        return {
            "timestamp": timestamp,
        }


class CodecError(Exception):
    """编解码错误"""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


# ==================== 工具函数 ====================


def timestamp_to_proto(dt: datetime | None) -> Any:
    """
    将 datetime 转换为 protobuf Timestamp

    Args:
        dt: datetime 实例

    Returns:
        protobuf Timestamp 消息
    """
    if dt is None:
        return None

    from antcode_contracts import common_pb2

    ts = common_pb2.Timestamp()
    ts.seconds = int(dt.timestamp())
    ts.nanos = int((dt.timestamp() % 1) * 1e9)
    return ts


def proto_to_timestamp(proto_ts: Any) -> datetime | None:
    """
    将 protobuf Timestamp 转换为 datetime

    Args:
        proto_ts: protobuf Timestamp 消息

    Returns:
        datetime 实例
    """
    if proto_ts is None:
        return None

    seconds = getattr(proto_ts, "seconds", 0)
    nanos = getattr(proto_ts, "nanos", 0)

    return datetime.fromtimestamp(seconds + nanos / 1e9)


def safe_get_field(proto_msg: Any, field: str, default: Any = None) -> Any:
    """
    安全获取 protobuf 消息字段

    Args:
        proto_msg: protobuf 消息
        field: 字段名
        default: 默认值

    Returns:
        字段值或默认值
    """
    try:
        if hasattr(proto_msg, field):
            value = getattr(proto_msg, field)
            # 检查是否为空消息
            if hasattr(value, "ByteSize") and value.ByteSize() == 0:
                return default
            return value
        return default
    except Exception:
        return default
