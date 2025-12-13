import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class NodeMessage(_message.Message):
    __slots__ = ("heartbeat", "log_batch", "task_status", "task_ack", "cancel_ack")
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    LOG_BATCH_FIELD_NUMBER: _ClassVar[int]
    TASK_STATUS_FIELD_NUMBER: _ClassVar[int]
    TASK_ACK_FIELD_NUMBER: _ClassVar[int]
    CANCEL_ACK_FIELD_NUMBER: _ClassVar[int]
    heartbeat: Heartbeat
    log_batch: LogBatch
    task_status: TaskStatus
    task_ack: TaskAck
    cancel_ack: CancelAck
    def __init__(self, heartbeat: _Optional[_Union[Heartbeat, _Mapping]] = ..., log_batch: _Optional[_Union[LogBatch, _Mapping]] = ..., task_status: _Optional[_Union[TaskStatus, _Mapping]] = ..., task_ack: _Optional[_Union[TaskAck, _Mapping]] = ..., cancel_ack: _Optional[_Union[CancelAck, _Mapping]] = ...) -> None: ...

class MasterMessage(_message.Message):
    __slots__ = ("task_dispatch", "task_cancel", "config_update", "ping")
    TASK_DISPATCH_FIELD_NUMBER: _ClassVar[int]
    TASK_CANCEL_FIELD_NUMBER: _ClassVar[int]
    CONFIG_UPDATE_FIELD_NUMBER: _ClassVar[int]
    PING_FIELD_NUMBER: _ClassVar[int]
    task_dispatch: TaskDispatch
    task_cancel: TaskCancel
    config_update: ConfigUpdate
    ping: Ping
    def __init__(self, task_dispatch: _Optional[_Union[TaskDispatch, _Mapping]] = ..., task_cancel: _Optional[_Union[TaskCancel, _Mapping]] = ..., config_update: _Optional[_Union[ConfigUpdate, _Mapping]] = ..., ping: _Optional[_Union[Ping, _Mapping]] = ...) -> None: ...

class Heartbeat(_message.Message):
    __slots__ = ("node_id", "status", "metrics", "os_info", "timestamp", "capabilities")
    class CapabilitiesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    NODE_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    METRICS_FIELD_NUMBER: _ClassVar[int]
    OS_INFO_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    node_id: str
    status: str
    metrics: _common_pb2.Metrics
    os_info: _common_pb2.OSInfo
    timestamp: _common_pb2.Timestamp
    capabilities: _containers.ScalarMap[str, str]
    def __init__(self, node_id: _Optional[str] = ..., status: _Optional[str] = ..., metrics: _Optional[_Union[_common_pb2.Metrics, _Mapping]] = ..., os_info: _Optional[_Union[_common_pb2.OSInfo, _Mapping]] = ..., timestamp: _Optional[_Union[_common_pb2.Timestamp, _Mapping]] = ..., capabilities: _Optional[_Mapping[str, str]] = ...) -> None: ...

class LogBatch(_message.Message):
    __slots__ = ("logs", "compressed", "compressed_data")
    LOGS_FIELD_NUMBER: _ClassVar[int]
    COMPRESSED_FIELD_NUMBER: _ClassVar[int]
    COMPRESSED_DATA_FIELD_NUMBER: _ClassVar[int]
    logs: _containers.RepeatedCompositeFieldContainer[LogEntry]
    compressed: bool
    compressed_data: bytes
    def __init__(self, logs: _Optional[_Iterable[_Union[LogEntry, _Mapping]]] = ..., compressed: bool = ..., compressed_data: _Optional[bytes] = ...) -> None: ...

class LogEntry(_message.Message):
    __slots__ = ("execution_id", "log_type", "content", "timestamp")
    EXECUTION_ID_FIELD_NUMBER: _ClassVar[int]
    LOG_TYPE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    execution_id: str
    log_type: str
    content: str
    timestamp: _common_pb2.Timestamp
    def __init__(self, execution_id: _Optional[str] = ..., log_type: _Optional[str] = ..., content: _Optional[str] = ..., timestamp: _Optional[_Union[_common_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class TaskStatus(_message.Message):
    __slots__ = ("execution_id", "status", "exit_code", "error_message", "timestamp")
    EXECUTION_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EXIT_CODE_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    execution_id: str
    status: str
    exit_code: int
    error_message: str
    timestamp: _common_pb2.Timestamp
    def __init__(self, execution_id: _Optional[str] = ..., status: _Optional[str] = ..., exit_code: _Optional[int] = ..., error_message: _Optional[str] = ..., timestamp: _Optional[_Union[_common_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class TaskDispatch(_message.Message):
    __slots__ = ("task_id", "project_id", "project_type", "priority", "params", "environment", "timeout", "download_url", "file_hash", "entry_point")
    class ParamsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class EnvironmentEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    PARAMS_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_FIELD_NUMBER: _ClassVar[int]
    DOWNLOAD_URL_FIELD_NUMBER: _ClassVar[int]
    FILE_HASH_FIELD_NUMBER: _ClassVar[int]
    ENTRY_POINT_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    project_id: str
    project_type: str
    priority: int
    params: _containers.ScalarMap[str, str]
    environment: _containers.ScalarMap[str, str]
    timeout: int
    download_url: str
    file_hash: str
    entry_point: str
    def __init__(self, task_id: _Optional[str] = ..., project_id: _Optional[str] = ..., project_type: _Optional[str] = ..., priority: _Optional[int] = ..., params: _Optional[_Mapping[str, str]] = ..., environment: _Optional[_Mapping[str, str]] = ..., timeout: _Optional[int] = ..., download_url: _Optional[str] = ..., file_hash: _Optional[str] = ..., entry_point: _Optional[str] = ...) -> None: ...

class TaskAck(_message.Message):
    __slots__ = ("task_id", "accepted", "reason")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    accepted: bool
    reason: str
    def __init__(self, task_id: _Optional[str] = ..., accepted: bool = ..., reason: _Optional[str] = ...) -> None: ...

class TaskCancel(_message.Message):
    __slots__ = ("task_id", "execution_id")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    execution_id: str
    def __init__(self, task_id: _Optional[str] = ..., execution_id: _Optional[str] = ...) -> None: ...

class CancelAck(_message.Message):
    __slots__ = ("task_id", "success", "reason")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    success: bool
    reason: str
    def __init__(self, task_id: _Optional[str] = ..., success: bool = ..., reason: _Optional[str] = ...) -> None: ...

class RegisterRequest(_message.Message):
    __slots__ = ("machine_code", "api_key", "node_id", "os_info", "capabilities")
    class CapabilitiesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    MACHINE_CODE_FIELD_NUMBER: _ClassVar[int]
    API_KEY_FIELD_NUMBER: _ClassVar[int]
    NODE_ID_FIELD_NUMBER: _ClassVar[int]
    OS_INFO_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    machine_code: str
    api_key: str
    node_id: str
    os_info: _common_pb2.OSInfo
    capabilities: _containers.ScalarMap[str, str]
    def __init__(self, machine_code: _Optional[str] = ..., api_key: _Optional[str] = ..., node_id: _Optional[str] = ..., os_info: _Optional[_Union[_common_pb2.OSInfo, _Mapping]] = ..., capabilities: _Optional[_Mapping[str, str]] = ...) -> None: ...

class RegisterResponse(_message.Message):
    __slots__ = ("success", "node_id", "error", "heartbeat_interval")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    NODE_ID_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_INTERVAL_FIELD_NUMBER: _ClassVar[int]
    success: bool
    node_id: str
    error: str
    heartbeat_interval: int
    def __init__(self, success: bool = ..., node_id: _Optional[str] = ..., error: _Optional[str] = ..., heartbeat_interval: _Optional[int] = ...) -> None: ...

class ConfigUpdate(_message.Message):
    __slots__ = ("config",)
    class ConfigEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    config: _containers.ScalarMap[str, str]
    def __init__(self, config: _Optional[_Mapping[str, str]] = ...) -> None: ...

class Ping(_message.Message):
    __slots__ = ("timestamp",)
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    timestamp: _common_pb2.Timestamp
    def __init__(self, timestamp: _Optional[_Union[_common_pb2.Timestamp, _Mapping]] = ...) -> None: ...
