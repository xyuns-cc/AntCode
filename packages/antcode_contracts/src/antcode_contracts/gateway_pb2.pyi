import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class WorkerMessage(_message.Message):
    __slots__ = ("heartbeat", "task_status", "task_ack", "cancel_ack")
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    TASK_STATUS_FIELD_NUMBER: _ClassVar[int]
    TASK_ACK_FIELD_NUMBER: _ClassVar[int]
    CANCEL_ACK_FIELD_NUMBER: _ClassVar[int]
    heartbeat: Heartbeat
    task_status: TaskStatus
    task_ack: TaskAck
    cancel_ack: CancelAck
    def __init__(self, heartbeat: _Optional[_Union[Heartbeat, _Mapping]] = ..., task_status: _Optional[_Union[TaskStatus, _Mapping]] = ..., task_ack: _Optional[_Union[TaskAck, _Mapping]] = ..., cancel_ack: _Optional[_Union[CancelAck, _Mapping]] = ...) -> None: ...

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
    __slots__ = ("worker_id", "status", "metrics", "os_info", "timestamp", "capabilities", "version")
    class CapabilitiesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    METRICS_FIELD_NUMBER: _ClassVar[int]
    OS_INFO_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    status: str
    metrics: _common_pb2.Metrics
    os_info: _common_pb2.OSInfo
    timestamp: _common_pb2.Timestamp
    capabilities: _containers.ScalarMap[str, str]
    version: str
    def __init__(self, worker_id: _Optional[str] = ..., status: _Optional[str] = ..., metrics: _Optional[_Union[_common_pb2.Metrics, _Mapping]] = ..., os_info: _Optional[_Union[_common_pb2.OSInfo, _Mapping]] = ..., timestamp: _Optional[_Union[_common_pb2.Timestamp, _Mapping]] = ..., capabilities: _Optional[_Mapping[str, str]] = ..., version: _Optional[str] = ...) -> None: ...

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
    __slots__ = ("task_id", "project_id", "project_type", "priority", "params", "environment", "timeout", "download_url", "file_hash", "entry_point", "run_id")
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
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
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
    run_id: str
    def __init__(self, task_id: _Optional[str] = ..., project_id: _Optional[str] = ..., project_type: _Optional[str] = ..., priority: _Optional[int] = ..., params: _Optional[_Mapping[str, str]] = ..., environment: _Optional[_Mapping[str, str]] = ..., timeout: _Optional[int] = ..., download_url: _Optional[str] = ..., file_hash: _Optional[str] = ..., entry_point: _Optional[str] = ..., run_id: _Optional[str] = ...) -> None: ...

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
    __slots__ = ("api_key", "worker_id", "os_info", "capabilities")
    class CapabilitiesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    API_KEY_FIELD_NUMBER: _ClassVar[int]
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    OS_INFO_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    api_key: str
    worker_id: str
    os_info: _common_pb2.OSInfo
    capabilities: _containers.ScalarMap[str, str]
    def __init__(self, api_key: _Optional[str] = ..., worker_id: _Optional[str] = ..., os_info: _Optional[_Union[_common_pb2.OSInfo, _Mapping]] = ..., capabilities: _Optional[_Mapping[str, str]] = ...) -> None: ...

class RegisterResponse(_message.Message):
    __slots__ = ("success", "worker_id", "error", "heartbeat_interval")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_INTERVAL_FIELD_NUMBER: _ClassVar[int]
    success: bool
    worker_id: str
    error: str
    heartbeat_interval: int
    def __init__(self, success: bool = ..., worker_id: _Optional[str] = ..., error: _Optional[str] = ..., heartbeat_interval: _Optional[int] = ...) -> None: ...

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

class PollTaskRequest(_message.Message):
    __slots__ = ("worker_id", "timeout_ms")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_MS_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    timeout_ms: int
    def __init__(self, worker_id: _Optional[str] = ..., timeout_ms: _Optional[int] = ...) -> None: ...

class PollTaskResponse(_message.Message):
    __slots__ = ("has_task", "task", "receipt_id")
    HAS_TASK_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_ID_FIELD_NUMBER: _ClassVar[int]
    has_task: bool
    task: TaskDispatch
    receipt_id: str
    def __init__(self, has_task: bool = ..., task: _Optional[_Union[TaskDispatch, _Mapping]] = ..., receipt_id: _Optional[str] = ...) -> None: ...

class AckTaskRequest(_message.Message):
    __slots__ = ("worker_id", "receipt_id", "accepted", "reason", "task_id")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_ID_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    receipt_id: str
    accepted: bool
    reason: str
    task_id: str
    def __init__(self, worker_id: _Optional[str] = ..., receipt_id: _Optional[str] = ..., accepted: bool = ..., reason: _Optional[str] = ..., task_id: _Optional[str] = ...) -> None: ...

class AckTaskResponse(_message.Message):
    __slots__ = ("success", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error: str
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...

class ReportResultRequest(_message.Message):
    __slots__ = ("run_id", "task_id", "worker_id", "status", "exit_code", "error_message", "started_at", "finished_at", "duration_ms", "data_json")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EXIT_CODE_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    FINISHED_AT_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    DATA_JSON_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    task_id: str
    worker_id: str
    status: str
    exit_code: int
    error_message: str
    started_at: str
    finished_at: str
    duration_ms: int
    data_json: str
    def __init__(self, run_id: _Optional[str] = ..., task_id: _Optional[str] = ..., worker_id: _Optional[str] = ..., status: _Optional[str] = ..., exit_code: _Optional[int] = ..., error_message: _Optional[str] = ..., started_at: _Optional[str] = ..., finished_at: _Optional[str] = ..., duration_ms: _Optional[int] = ..., data_json: _Optional[str] = ...) -> None: ...

class ReportResultResponse(_message.Message):
    __slots__ = ("success", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error: str
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...

class SendLogRequest(_message.Message):
    __slots__ = ("execution_id", "log_type", "content", "timestamp", "sequence")
    EXECUTION_ID_FIELD_NUMBER: _ClassVar[int]
    LOG_TYPE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    execution_id: str
    log_type: str
    content: str
    timestamp: str
    sequence: int
    def __init__(self, execution_id: _Optional[str] = ..., log_type: _Optional[str] = ..., content: _Optional[str] = ..., timestamp: _Optional[str] = ..., sequence: _Optional[int] = ...) -> None: ...

class LogEntry(_message.Message):
    __slots__ = ("execution_id", "log_type", "content", "timestamp", "sequence")
    EXECUTION_ID_FIELD_NUMBER: _ClassVar[int]
    LOG_TYPE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    execution_id: str
    log_type: str
    content: str
    timestamp: str
    sequence: int
    def __init__(self, execution_id: _Optional[str] = ..., log_type: _Optional[str] = ..., content: _Optional[str] = ..., timestamp: _Optional[str] = ..., sequence: _Optional[int] = ...) -> None: ...

class SendLogBatchRequest(_message.Message):
    __slots__ = ("logs",)
    LOGS_FIELD_NUMBER: _ClassVar[int]
    logs: _containers.RepeatedCompositeFieldContainer[LogEntry]
    def __init__(self, logs: _Optional[_Iterable[_Union[LogEntry, _Mapping]]] = ...) -> None: ...

class SendLogResponse(_message.Message):
    __slots__ = ("success", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error: str
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...

class SendLogBatchResponse(_message.Message):
    __slots__ = ("success", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error: str
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...

class SendLogChunkRequest(_message.Message):
    __slots__ = ("execution_id", "log_type", "data", "offset", "is_final", "checksum", "total_size")
    EXECUTION_ID_FIELD_NUMBER: _ClassVar[int]
    LOG_TYPE_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    IS_FINAL_FIELD_NUMBER: _ClassVar[int]
    CHECKSUM_FIELD_NUMBER: _ClassVar[int]
    TOTAL_SIZE_FIELD_NUMBER: _ClassVar[int]
    execution_id: str
    log_type: str
    data: bytes
    offset: int
    is_final: bool
    checksum: str
    total_size: int
    def __init__(self, execution_id: _Optional[str] = ..., log_type: _Optional[str] = ..., data: _Optional[bytes] = ..., offset: _Optional[int] = ..., is_final: bool = ..., checksum: _Optional[str] = ..., total_size: _Optional[int] = ...) -> None: ...

class SendLogChunkResponse(_message.Message):
    __slots__ = ("success", "ack_offset", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ACK_OFFSET_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    ack_offset: int
    error: str
    def __init__(self, success: bool = ..., ack_offset: _Optional[int] = ..., error: _Optional[str] = ...) -> None: ...

class SendHeartbeatRequest(_message.Message):
    __slots__ = ("worker_id", "status", "cpu_percent", "memory_percent", "disk_percent", "running_tasks", "max_concurrent_tasks", "timestamp", "version")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    CPU_PERCENT_FIELD_NUMBER: _ClassVar[int]
    MEMORY_PERCENT_FIELD_NUMBER: _ClassVar[int]
    DISK_PERCENT_FIELD_NUMBER: _ClassVar[int]
    RUNNING_TASKS_FIELD_NUMBER: _ClassVar[int]
    MAX_CONCURRENT_TASKS_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    status: str
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    running_tasks: int
    max_concurrent_tasks: int
    timestamp: str
    version: str
    def __init__(self, worker_id: _Optional[str] = ..., status: _Optional[str] = ..., cpu_percent: _Optional[float] = ..., memory_percent: _Optional[float] = ..., disk_percent: _Optional[float] = ..., running_tasks: _Optional[int] = ..., max_concurrent_tasks: _Optional[int] = ..., timestamp: _Optional[str] = ..., version: _Optional[str] = ...) -> None: ...

class SendHeartbeatResponse(_message.Message):
    __slots__ = ("success", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error: str
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...

class ControlMessage(_message.Message):
    __slots__ = ("task_cancel", "config_update", "runtime_control")
    TASK_CANCEL_FIELD_NUMBER: _ClassVar[int]
    CONFIG_UPDATE_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_CONTROL_FIELD_NUMBER: _ClassVar[int]
    task_cancel: TaskCancel
    config_update: ConfigUpdate
    runtime_control: RuntimeControl
    def __init__(self, task_cancel: _Optional[_Union[TaskCancel, _Mapping]] = ..., config_update: _Optional[_Union[ConfigUpdate, _Mapping]] = ..., runtime_control: _Optional[_Union[RuntimeControl, _Mapping]] = ...) -> None: ...

class RuntimeControl(_message.Message):
    __slots__ = ("request_id", "action", "reply_stream", "payload_json")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    REPLY_STREAM_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_JSON_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    action: str
    reply_stream: str
    payload_json: str
    def __init__(self, request_id: _Optional[str] = ..., action: _Optional[str] = ..., reply_stream: _Optional[str] = ..., payload_json: _Optional[str] = ...) -> None: ...

class PollControlRequest(_message.Message):
    __slots__ = ("worker_id", "timeout_ms")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_MS_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    timeout_ms: int
    def __init__(self, worker_id: _Optional[str] = ..., timeout_ms: _Optional[int] = ...) -> None: ...

class PollControlResponse(_message.Message):
    __slots__ = ("has_control", "control", "receipt_id")
    HAS_CONTROL_FIELD_NUMBER: _ClassVar[int]
    CONTROL_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_ID_FIELD_NUMBER: _ClassVar[int]
    has_control: bool
    control: ControlMessage
    receipt_id: str
    def __init__(self, has_control: bool = ..., control: _Optional[_Union[ControlMessage, _Mapping]] = ..., receipt_id: _Optional[str] = ...) -> None: ...

class AckControlRequest(_message.Message):
    __slots__ = ("worker_id", "receipt_id")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_ID_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    receipt_id: str
    def __init__(self, worker_id: _Optional[str] = ..., receipt_id: _Optional[str] = ...) -> None: ...

class AckControlResponse(_message.Message):
    __slots__ = ("success", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error: str
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...

class ControlResultRequest(_message.Message):
    __slots__ = ("worker_id", "request_id", "success", "payload_json", "error", "reply_stream")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_JSON_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    REPLY_STREAM_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    request_id: str
    success: bool
    payload_json: str
    error: str
    reply_stream: str
    def __init__(self, worker_id: _Optional[str] = ..., request_id: _Optional[str] = ..., success: bool = ..., payload_json: _Optional[str] = ..., error: _Optional[str] = ..., reply_stream: _Optional[str] = ...) -> None: ...

class ControlResultResponse(_message.Message):
    __slots__ = ("success", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error: str
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...
