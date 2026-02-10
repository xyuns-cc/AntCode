"""
Redis 消息编解码模块

实现 Worker 与 Redis 之间消息的序列化和反序列化。
支持 schema version 标记，便于协议演进。

Requirements: 5.4
"""

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, TypeVar

from antcode_worker.domain.enums import (
    ArtifactType,
    ExitReason,
    LogStream,
    RunStatus,
    TaskType,
)
from antcode_worker.domain.models import (
    ArtifactRef,
    ExecResult,
    LogEntry,
    RunContext,
    RuntimeSpec,
    TaskPayload,
)

T = TypeVar("T")


class SchemaVersion(str, Enum):
    """消息 schema 版本"""

    V1 = "v1"  # 初始版本
    V2 = "v2"  # 预留扩展版本

    @classmethod
    def current(cls) -> "SchemaVersion":
        """获取当前版本"""
        return cls.V1


class CodecError(Exception):
    """编解码错误"""

    pass


class MessageCodec(ABC):
    """消息编解码器基类"""

    @abstractmethod
    def encode(self, obj: Any) -> dict[str, str]:
        """
        编码对象为 Redis Stream 消息格式

        Args:
            obj: 要编码的对象

        Returns:
            字符串键值对字典（Redis Stream 要求）
        """
        pass

    @abstractmethod
    def decode(self, data: dict[str, str], target_type: type[T]) -> T:
        """
        解码 Redis Stream 消息为对象

        Args:
            data: Redis Stream 消息数据
            target_type: 目标类型

        Returns:
            解码后的对象
        """
        pass


class JsonCodec(MessageCodec):
    """
    JSON 编解码器

    使用 JSON 序列化，并携带 schema version 字段。
    """

    # 版本字段名
    VERSION_FIELD: ClassVar[str] = "_schema_version"

    def __init__(self, version: SchemaVersion | None = None):
        """
        初始化编解码器

        Args:
            version: schema 版本，默认使用当前版本
        """
        self._version = version or SchemaVersion.current()

    @property
    def version(self) -> SchemaVersion:
        """获取 schema 版本"""
        return self._version

    def encode(self, obj: Any) -> dict[str, str]:
        """
        编码对象为 Redis Stream 消息格式

        Args:
            obj: 要编码的对象

        Returns:
            字符串键值对字典
        """
        try:
            # 转换为字典
            if is_dataclass(obj) and not isinstance(obj, type):
                data = self._dataclass_to_dict(obj)
            elif hasattr(obj, "to_dict"):
                data = obj.to_dict()
            elif isinstance(obj, dict):
                data = obj
            else:
                raise CodecError(f"Unsupported type for encoding: {type(obj)}")

            # 添加版本信息
            data[self.VERSION_FIELD] = self._version.value

            # 转换为字符串值
            return self._dict_to_string_dict(data)

        except Exception as e:
            raise CodecError(f"Failed to encode object: {e}") from e

    def decode(self, data: dict[str, str], target_type: type[T]) -> T:
        """
        解码 Redis Stream 消息为对象

        Args:
            data: Redis Stream 消息数据
            target_type: 目标类型

        Returns:
            解码后的对象
        """
        try:
            # 解析字符串值
            parsed = self._string_dict_to_dict(data)

            # 检查版本
            version_str = parsed.pop(self.VERSION_FIELD, SchemaVersion.V1.value)
            version = SchemaVersion(version_str)

            # 根据版本进行迁移
            parsed = self._migrate_schema(parsed, version, target_type)

            # 构造目标对象
            return self._dict_to_object(parsed, target_type)

        except Exception as e:
            raise CodecError(f"Failed to decode to {target_type.__name__}: {e}") from e

    def _dataclass_to_dict(self, obj: Any) -> dict[str, Any]:
        """将 dataclass 转换为字典，处理嵌套类型"""
        result = {}
        for key, value in asdict(obj).items():
            result[key] = self._serialize_value(value)
        return result

    def _serialize_value(self, value: Any) -> Any:
        """序列化单个值"""
        if value is None:
            return None
        elif isinstance(value, datetime):
            return value.isoformat()
        elif isinstance(value, Enum):
            return value.value
        elif isinstance(value, (list, tuple)):
            return [self._serialize_value(v) for v in value]
        elif isinstance(value, dict):
            return {k: self._serialize_value(v) for k, v in value.items()}
        elif is_dataclass(value) and not isinstance(value, type):
            return self._dataclass_to_dict(value)
        else:
            return value

    def _dict_to_string_dict(self, data: dict[str, Any]) -> dict[str, str]:
        """将字典转换为字符串值字典（Redis Stream 要求）"""
        result = {}
        for key, value in data.items():
            if value is None:
                result[key] = ""
            elif isinstance(value, str):
                result[key] = value
            elif isinstance(value, bool):
                result[key] = "true" if value else "false"
            elif isinstance(value, (int, float)):
                result[key] = str(value)
            elif isinstance(value, (dict, list)):
                result[key] = json.dumps(value, ensure_ascii=False)
            else:
                result[key] = str(value)
        return result

    def _string_dict_to_dict(self, data: dict[str, str]) -> dict[str, Any]:
        """将字符串值字典解析为普通字典"""
        result = {}
        for key, value in data.items():
            result[key] = self._parse_string_value(value)
        return result

    def _parse_string_value(self, value: str) -> Any:
        """解析字符串值"""
        if value == "":
            return None
        if value == "true":
            return True
        if value == "false":
            return False

        # 尝试解析为数字
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            pass

        # 尝试解析为 JSON
        if value.startswith(("{", "[")):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass

        return value

    def _migrate_schema(
        self, data: dict[str, Any], from_version: SchemaVersion, target_type: type
    ) -> dict[str, Any]:
        """
        迁移 schema 版本

        Args:
            data: 原始数据
            from_version: 源版本
            target_type: 目标类型

        Returns:
            迁移后的数据
        """
        # 目前只有 V1，无需迁移
        # 未来添加新版本时，在这里实现迁移逻辑
        if from_version == SchemaVersion.V1:
            return data

        # V2 迁移示例（预留）
        # if from_version == SchemaVersion.V2:
        #     # 执行 V2 -> V1 的迁移
        #     pass

        return data

    def _dict_to_object(self, data: dict[str, Any], target_type: type[T]) -> T:
        """将字典转换为目标类型对象"""
        if target_type == RunContext:
            return self._decode_run_context(data)
        elif target_type == TaskPayload:
            return self._decode_task_payload(data)
        elif target_type == ExecResult:
            return self._decode_exec_result(data)
        elif target_type == LogEntry:
            return self._decode_log_entry(data)
        elif target_type == ArtifactRef:
            return self._decode_artifact_ref(data)
        elif target_type is dict:
            return data  # type: ignore
        else:
            # 尝试直接构造
            return target_type(**data)

    def _decode_run_context(self, data: dict[str, Any]) -> RunContext:
        """解码 RunContext"""
        # 处理 runtime_spec
        runtime_spec = None
        if data.get("runtime_spec"):
            runtime_spec = RuntimeSpec(**data["runtime_spec"])

        # 处理时间字段
        created_at = None
        if data.get("created_at"):
            created_at = datetime.fromisoformat(data["created_at"])

        return RunContext(
            run_id=data.get("run_id", ""),
            task_id=data.get("task_id", ""),
            project_id=data.get("project_id", ""),
            runtime_spec=runtime_spec,
            timeout_seconds=data.get("timeout_seconds", 3600),
            memory_limit_mb=data.get("memory_limit_mb", 0),
            cpu_limit_seconds=data.get("cpu_limit_seconds", 0),
            priority=data.get("priority", 0),
            labels=data.get("labels", {}),
            created_at=created_at,
            receipt=data.get("receipt"),
        )

    def _decode_task_payload(self, data: dict[str, Any]) -> TaskPayload:
        """解码 TaskPayload"""
        task_type = TaskType.CODE
        if data.get("task_type"):
            task_type = TaskType(data["task_type"])

        return TaskPayload(
            task_type=task_type,
            project_path=data.get("project_path"),
            download_url=data.get("download_url"),
            file_hash=data.get("file_hash"),
            entry_point=data.get("entry_point", ""),
            function=data.get("function"),
            args=data.get("args", []),
            kwargs=data.get("kwargs", {}),
            env_vars=data.get("env_vars", {}),
            inputs=data.get("inputs", {}),
            artifact_patterns=data.get("artifact_patterns", []),
        )

    def _decode_exec_result(self, data: dict[str, Any]) -> ExecResult:
        """解码 ExecResult"""
        status = RunStatus(data.get("status", "pending"))
        exit_reason = ExitReason(data.get("exit_reason", "normal"))

        # 处理时间字段
        started_at = None
        if data.get("started_at"):
            started_at = datetime.fromisoformat(data["started_at"])

        finished_at = None
        if data.get("finished_at"):
            finished_at = datetime.fromisoformat(data["finished_at"])

        # 处理 artifacts
        artifacts = []
        for artifact_data in data.get("artifacts", []):
            artifacts.append(self._decode_artifact_ref(artifact_data))

        return ExecResult(
            run_id=data.get("run_id", ""),
            status=status,
            exit_code=data.get("exit_code"),
            exit_reason=exit_reason,
            error_message=data.get("error_message"),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=data.get("duration_ms", 0),
            cpu_time_seconds=data.get("cpu_time_seconds", 0),
            memory_peak_mb=data.get("memory_peak_mb", 0),
            artifacts=artifacts,
            stdout_lines=data.get("stdout_lines", 0),
            stderr_lines=data.get("stderr_lines", 0),
            log_archived=data.get("log_archived", False),
            log_archive_uri=data.get("log_archive_uri"),
            data=data.get("data", {}),
        )

    def _decode_log_entry(self, data: dict[str, Any]) -> LogEntry:
        """解码 LogEntry"""
        stream = LogStream(data.get("stream", "stdout"))

        timestamp = None
        if data.get("timestamp"):
            timestamp = datetime.fromisoformat(data["timestamp"])

        return LogEntry(
            run_id=data.get("run_id", ""),
            stream=stream,
            content=data.get("content", ""),
            seq=data.get("seq", 0),
            timestamp=timestamp,
            level=data.get("level", "INFO"),
            source=data.get("source"),
        )

    def _decode_artifact_ref(self, data: dict[str, Any]) -> ArtifactRef:
        """解码 ArtifactRef"""
        artifact_type = ArtifactType.FILE
        if data.get("type"):
            artifact_type = ArtifactType(data["type"])
        elif data.get("artifact_type"):
            artifact_type = ArtifactType(data["artifact_type"])

        created_at = None
        if data.get("created_at"):
            created_at = datetime.fromisoformat(data["created_at"])

        return ArtifactRef(
            name=data.get("name", ""),
            artifact_type=artifact_type,
            uri=data.get("uri"),
            local_path=data.get("local_path"),
            size_bytes=data.get("size_bytes", 0),
            checksum=data.get("checksum"),
            mime_type=data.get("mime_type"),
            created_at=created_at,
        )


class TaskMessageCodec(JsonCodec):
    """
    任务消息编解码器

    专门用于任务相关消息的编解码。
    """

    # 任务消息字段映射
    FIELD_MAPPINGS: ClassVar[dict[str, str]] = {
        "task_id": "task_id",
        "run_id": "run_id",
        "project_id": "project_id",
        "task_type": "task_type",
        "priority": "priority",
        "timeout": "timeout_seconds",
        "payload": "payload",
        "receipt": "receipt",
    }

    def encode_task_dispatch(
        self,
        run_id: str,
        task_id: str,
        project_id: str,
        payload: TaskPayload,
        context: RunContext,
    ) -> dict[str, str]:
        """
        编码任务分发消息

        Args:
            run_id: 运行 ID
            task_id: 任务 ID
            project_id: 项目 ID
            payload: 任务数据
            context: 执行上下文

        Returns:
            编码后的消息
        """
        data = {
            "run_id": run_id,
            "task_id": task_id,
            "project_id": project_id,
            "task_type": payload.task_type.value,
            "priority": context.priority,
            "timeout_seconds": context.timeout_seconds,
            "payload": self._dataclass_to_dict(payload),
            "context": self._dataclass_to_dict(context),
        }
        return self.encode(data)

    def decode_task_dispatch(
        self, data: dict[str, str]
    ) -> tuple[TaskPayload, RunContext]:
        """
        解码任务分发消息

        Args:
            data: 消息数据

        Returns:
            (TaskPayload, RunContext) 元组
        """
        parsed = self._string_dict_to_dict(data)

        # 移除版本字段
        parsed.pop(self.VERSION_FIELD, None)

        # 解码 payload
        payload_data = parsed.get("payload", {})
        payload = self._decode_task_payload(payload_data)

        # 解码 context
        context_data = parsed.get("context", {})
        # 补充基本字段
        context_data["run_id"] = parsed.get("run_id", context_data.get("run_id", ""))
        context_data["task_id"] = parsed.get("task_id", context_data.get("task_id", ""))
        context_data["project_id"] = parsed.get(
            "project_id", context_data.get("project_id", "")
        )
        context = self._decode_run_context(context_data)

        return payload, context


class LogMessageCodec(JsonCodec):
    """
    日志消息编解码器

    专门用于日志消息的编解码，优化批量处理。
    """

    def encode_log_entry(self, entry: LogEntry) -> dict[str, str]:
        """编码单条日志"""
        return self.encode(entry)

    def encode_log_batch(self, entries: list[LogEntry]) -> list[dict[str, str]]:
        """编码日志批次"""
        return [self.encode_log_entry(entry) for entry in entries]

    def decode_log_entry(self, data: dict[str, str]) -> LogEntry:
        """解码单条日志"""
        return self.decode(data, LogEntry)

    def decode_log_batch(self, batch: list[dict[str, str]]) -> list[LogEntry]:
        """解码日志批次"""
        return [self.decode_log_entry(data) for data in batch]


class ResultMessageCodec(JsonCodec):
    """
    结果消息编解码器

    专门用于执行结果消息的编解码。
    """

    def encode_result(self, result: ExecResult) -> dict[str, str]:
        """编码执行结果"""
        return self.encode(result)

    def decode_result(self, data: dict[str, str]) -> ExecResult:
        """解码执行结果"""
        return self.decode(data, ExecResult)


class HeartbeatCodec(JsonCodec):
    """
    心跳消息编解码器

    专门用于心跳消息的编解码。
    """

    def encode_heartbeat(
        self,
        worker_id: str,
        status: str,
        cpu_percent: float,
        memory_percent: float,
        disk_percent: float,
        running_tasks: int,
        max_concurrent_tasks: int,
        queue_depth: int = 0,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """
        编码心跳消息

        Args:
            worker_id: Worker ID
            status: 状态
            cpu_percent: CPU 使用率
            memory_percent: 内存使用率
            disk_percent: 磁盘使用率
            running_tasks: 运行中任务数
            max_concurrent_tasks: 最大并发任务数
            queue_depth: 队列深度
            extra: 额外信息

        Returns:
            编码后的消息
        """
        data = {
            "worker_id": worker_id,
            "status": status,
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "disk_percent": disk_percent,
            "running_tasks": running_tasks,
            "max_concurrent_tasks": max_concurrent_tasks,
            "queue_depth": queue_depth,
            "timestamp": datetime.now().isoformat(),
        }
        if extra:
            data["extra"] = extra
        return self.encode(data)

    def decode_heartbeat(self, data: dict[str, str]) -> dict[str, Any]:
        """解码心跳消息"""
        parsed = self._string_dict_to_dict(data)
        parsed.pop(self.VERSION_FIELD, None)
        return parsed


class ControlMessageCodec(JsonCodec):
    """
    控制消息编解码器

    用于取消、kill、配置更新等控制消息。
    """

    def encode_cancel(self, run_id: str, reason: str = "") -> dict[str, str]:
        """编码取消命令"""
        return self.encode(
            {
                "command": "cancel",
                "run_id": run_id,
                "reason": reason,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def encode_kill(self, run_id: str, reason: str = "") -> dict[str, str]:
        """编码强制终止命令"""
        return self.encode(
            {
                "command": "kill",
                "run_id": run_id,
                "reason": reason,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def encode_config_update(self, config: dict[str, Any]) -> dict[str, str]:
        """编码配置更新命令"""
        return self.encode(
            {
                "command": "config_update",
                "config": config,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def decode_control(self, data: dict[str, str]) -> dict[str, Any]:
        """解码控制消息"""
        parsed = self._string_dict_to_dict(data)
        parsed.pop(self.VERSION_FIELD, None)
        return parsed


# 默认编解码器实例
default_codec = JsonCodec()
task_codec = TaskMessageCodec()
log_codec = LogMessageCodec()
result_codec = ResultMessageCodec()
heartbeat_codec = HeartbeatCodec()
control_codec = ControlMessageCodec()
