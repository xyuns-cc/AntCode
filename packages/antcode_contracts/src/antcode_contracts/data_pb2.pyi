import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Status(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STATUS_UNSPECIFIED: _ClassVar[Status]
    STATUS_PENDING: _ClassVar[Status]
    STATUS_RUNNING: _ClassVar[Status]
    STATUS_COMPLETED: _ClassVar[Status]
    STATUS_FAILED: _ClassVar[Status]
    STATUS_CANCELLED: _ClassVar[Status]
    STATUS_TIMEOUT: _ClassVar[Status]

class LogType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    LOG_TYPE_UNSPECIFIED: _ClassVar[LogType]
    LOG_TYPE_STDOUT: _ClassVar[LogType]
    LOG_TYPE_STDERR: _ClassVar[LogType]
    LOG_TYPE_SYSTEM: _ClassVar[LogType]
STATUS_UNSPECIFIED: Status
STATUS_PENDING: Status
STATUS_RUNNING: Status
STATUS_COMPLETED: Status
STATUS_FAILED: Status
STATUS_CANCELLED: Status
STATUS_TIMEOUT: Status
LOG_TYPE_UNSPECIFIED: LogType
LOG_TYPE_STDOUT: LogType
LOG_TYPE_STDERR: LogType
LOG_TYPE_SYSTEM: LogType

class SubscribeRequest(_message.Message):
    __slots__ = ("worker_id", "capabilities", "prefetch", "trace")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    PREFETCH_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    capabilities: _containers.RepeatedScalarFieldContainer[str]
    prefetch: int
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., capabilities: _Optional[_Iterable[str]] = ..., prefetch: _Optional[int] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class TaskDispatch(_message.Message):
    __slots__ = ("task_id", "project_id", "project_type", "priority", "params", "environment", "timeout_seconds", "source_bundle_uri", "source_bundle_sha256", "source_bundle_size", "transfer_method", "resolved_revision", "source_subdir", "entry_point", "run_id", "receipt_id", "trace")
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
    TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    SOURCE_BUNDLE_URI_FIELD_NUMBER: _ClassVar[int]
    SOURCE_BUNDLE_SHA256_FIELD_NUMBER: _ClassVar[int]
    SOURCE_BUNDLE_SIZE_FIELD_NUMBER: _ClassVar[int]
    TRANSFER_METHOD_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_REVISION_FIELD_NUMBER: _ClassVar[int]
    SOURCE_SUBDIR_FIELD_NUMBER: _ClassVar[int]
    ENTRY_POINT_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_ID_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    project_id: str
    project_type: str
    priority: int
    params: _containers.ScalarMap[str, str]
    environment: _containers.ScalarMap[str, str]
    timeout_seconds: int
    source_bundle_uri: str
    source_bundle_sha256: str
    source_bundle_size: int
    transfer_method: str
    resolved_revision: str
    source_subdir: str
    entry_point: str
    run_id: str
    receipt_id: str
    trace: _common_pb2.TraceContext
    def __init__(self, task_id: _Optional[str] = ..., project_id: _Optional[str] = ..., project_type: _Optional[str] = ..., priority: _Optional[int] = ..., params: _Optional[_Mapping[str, str]] = ..., environment: _Optional[_Mapping[str, str]] = ..., timeout_seconds: _Optional[int] = ..., source_bundle_uri: _Optional[str] = ..., source_bundle_sha256: _Optional[str] = ..., source_bundle_size: _Optional[int] = ..., transfer_method: _Optional[str] = ..., resolved_revision: _Optional[str] = ..., source_subdir: _Optional[str] = ..., entry_point: _Optional[str] = ..., run_id: _Optional[str] = ..., receipt_id: _Optional[str] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class AckTaskRequest(_message.Message):
    __slots__ = ("worker_id", "receipt_id", "task_id", "accepted", "reason", "trace")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    receipt_id: str
    task_id: str
    accepted: bool
    reason: str
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., receipt_id: _Optional[str] = ..., task_id: _Optional[str] = ..., accepted: bool = ..., reason: _Optional[str] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class AckTaskResponse(_message.Message):
    __slots__ = ("success", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error: str
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...

class TaskStatus(_message.Message):
    __slots__ = ("run_id", "task_id", "worker_id", "status", "exit_code", "error_message", "started_at", "finished_at", "duration_ms", "data", "trace")
    class DataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EXIT_CODE_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    FINISHED_AT_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    task_id: str
    worker_id: str
    status: Status
    exit_code: int
    error_message: str
    started_at: _common_pb2.Timestamp
    finished_at: _common_pb2.Timestamp
    duration_ms: int
    data: _containers.ScalarMap[str, str]
    trace: _common_pb2.TraceContext
    def __init__(self, run_id: _Optional[str] = ..., task_id: _Optional[str] = ..., worker_id: _Optional[str] = ..., status: _Optional[_Union[Status, str]] = ..., exit_code: _Optional[int] = ..., error_message: _Optional[str] = ..., started_at: _Optional[_Union[_common_pb2.Timestamp, _Mapping]] = ..., finished_at: _Optional[_Union[_common_pb2.Timestamp, _Mapping]] = ..., duration_ms: _Optional[int] = ..., data: _Optional[_Mapping[str, str]] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class StatusAck(_message.Message):
    __slots__ = ("received",)
    RECEIVED_FIELD_NUMBER: _ClassVar[int]
    received: int
    def __init__(self, received: _Optional[int] = ...) -> None: ...

class LogBatch(_message.Message):
    __slots__ = ("worker_id", "entries", "trace")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    ENTRIES_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    entries: _containers.RepeatedCompositeFieldContainer[LogEntry]
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., entries: _Optional[_Iterable[_Union[LogEntry, _Mapping]]] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class LogEntry(_message.Message):
    __slots__ = ("run_id", "log_type", "content", "timestamp", "sequence")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    LOG_TYPE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    log_type: LogType
    content: str
    timestamp: _common_pb2.Timestamp
    sequence: int
    def __init__(self, run_id: _Optional[str] = ..., log_type: _Optional[_Union[LogType, str]] = ..., content: _Optional[str] = ..., timestamp: _Optional[_Union[_common_pb2.Timestamp, _Mapping]] = ..., sequence: _Optional[int] = ...) -> None: ...

class LogAck(_message.Message):
    __slots__ = ("received",)
    RECEIVED_FIELD_NUMBER: _ClassVar[int]
    received: int
    def __init__(self, received: _Optional[int] = ...) -> None: ...

class SpiderDataBatch(_message.Message):
    __slots__ = ("worker_id", "run_id", "project_id", "items", "meta", "trace")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    META_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    run_id: str
    project_id: str
    items: _containers.RepeatedCompositeFieldContainer[SpiderDataItem]
    meta: SpiderMetaUpdate
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., run_id: _Optional[str] = ..., project_id: _Optional[str] = ..., items: _Optional[_Iterable[_Union[SpiderDataItem, _Mapping]]] = ..., meta: _Optional[_Union[SpiderMetaUpdate, _Mapping]] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class SpiderDataItem(_message.Message):
    __slots__ = ("item_id", "spider_name", "item_type", "data", "url", "timestamp", "sequence")
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    SPIDER_NAME_FIELD_NUMBER: _ClassVar[int]
    ITEM_TYPE_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    item_id: str
    spider_name: str
    item_type: str
    data: bytes
    url: str
    timestamp: str
    sequence: int
    def __init__(self, item_id: _Optional[str] = ..., spider_name: _Optional[str] = ..., item_type: _Optional[str] = ..., data: _Optional[bytes] = ..., url: _Optional[str] = ..., timestamp: _Optional[str] = ..., sequence: _Optional[int] = ...) -> None: ...

class SpiderMetaUpdate(_message.Message):
    __slots__ = ("status", "items_count", "last_item_at")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ITEMS_COUNT_FIELD_NUMBER: _ClassVar[int]
    LAST_ITEM_AT_FIELD_NUMBER: _ClassVar[int]
    status: str
    items_count: int
    last_item_at: str
    def __init__(self, status: _Optional[str] = ..., items_count: _Optional[int] = ..., last_item_at: _Optional[str] = ...) -> None: ...

class SpiderDataAck(_message.Message):
    __slots__ = ("accepted", "failed")
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    FAILED_FIELD_NUMBER: _ClassVar[int]
    accepted: int
    failed: int
    def __init__(self, accepted: _Optional[int] = ..., failed: _Optional[int] = ...) -> None: ...
