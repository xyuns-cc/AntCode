import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class RegisterRequest(_message.Message):
    __slots__ = ("api_key", "worker_id", "os_info", "capabilities", "version", "trace")
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
    VERSION_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    api_key: str
    worker_id: str
    os_info: _common_pb2.OSInfo
    capabilities: _containers.ScalarMap[str, str]
    version: str
    trace: _common_pb2.TraceContext
    def __init__(self, api_key: _Optional[str] = ..., worker_id: _Optional[str] = ..., os_info: _Optional[_Union[_common_pb2.OSInfo, _Mapping]] = ..., capabilities: _Optional[_Mapping[str, str]] = ..., version: _Optional[str] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class RegisterResponse(_message.Message):
    __slots__ = ("success", "worker_id", "error", "lease_ttl_ms", "lease_renew_after_ms")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    LEASE_TTL_MS_FIELD_NUMBER: _ClassVar[int]
    LEASE_RENEW_AFTER_MS_FIELD_NUMBER: _ClassVar[int]
    success: bool
    worker_id: str
    error: str
    lease_ttl_ms: int
    lease_renew_after_ms: int
    def __init__(self, success: bool = ..., worker_id: _Optional[str] = ..., error: _Optional[str] = ..., lease_ttl_ms: _Optional[int] = ..., lease_renew_after_ms: _Optional[int] = ...) -> None: ...

class DeregisterRequest(_message.Message):
    __slots__ = ("worker_id", "reason", "trace")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    reason: str
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., reason: _Optional[str] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class DeregisterResponse(_message.Message):
    __slots__ = ("success", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error: str
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...

class LeaseRequest(_message.Message):
    __slots__ = ("worker_id", "current_lease_id", "metrics", "trace")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    CURRENT_LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    METRICS_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    current_lease_id: str
    metrics: _common_pb2.Metrics
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., current_lease_id: _Optional[str] = ..., metrics: _Optional[_Union[_common_pb2.Metrics, _Mapping]] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class LeaseResponse(_message.Message):
    __slots__ = ("lease_id", "expires_at_ms", "renew_after_ms", "revoked", "revoke_reason")
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_MS_FIELD_NUMBER: _ClassVar[int]
    RENEW_AFTER_MS_FIELD_NUMBER: _ClassVar[int]
    REVOKED_FIELD_NUMBER: _ClassVar[int]
    REVOKE_REASON_FIELD_NUMBER: _ClassVar[int]
    lease_id: str
    expires_at_ms: int
    renew_after_ms: int
    revoked: bool
    revoke_reason: str
    def __init__(self, lease_id: _Optional[str] = ..., expires_at_ms: _Optional[int] = ..., renew_after_ms: _Optional[int] = ..., revoked: bool = ..., revoke_reason: _Optional[str] = ...) -> None: ...

class CancelTaskRequest(_message.Message):
    __slots__ = ("worker_id", "task_id", "run_id", "reason", "trace")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    task_id: str
    run_id: str
    reason: str
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., task_id: _Optional[str] = ..., run_id: _Optional[str] = ..., reason: _Optional[str] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class CancelTaskResponse(_message.Message):
    __slots__ = ("success", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error: str
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...

class UpdateConfigRequest(_message.Message):
    __slots__ = ("worker_id", "config", "trace")
    class ConfigEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    config: _containers.ScalarMap[str, str]
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., config: _Optional[_Mapping[str, str]] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class UpdateConfigResponse(_message.Message):
    __slots__ = ("success", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error: str
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...

class WatchControlRequest(_message.Message):
    __slots__ = ("worker_id", "trace")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class ControlEvent(_message.Message):
    __slots__ = ("event_id", "trace", "task_cancel", "config_update", "runtime_control", "ping")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    TASK_CANCEL_FIELD_NUMBER: _ClassVar[int]
    CONFIG_UPDATE_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_CONTROL_FIELD_NUMBER: _ClassVar[int]
    PING_FIELD_NUMBER: _ClassVar[int]
    event_id: str
    trace: _common_pb2.TraceContext
    task_cancel: TaskCancel
    config_update: ConfigUpdate
    runtime_control: RuntimeControl
    ping: Ping
    def __init__(self, event_id: _Optional[str] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ..., task_cancel: _Optional[_Union[TaskCancel, _Mapping]] = ..., config_update: _Optional[_Union[ConfigUpdate, _Mapping]] = ..., runtime_control: _Optional[_Union[RuntimeControl, _Mapping]] = ..., ping: _Optional[_Union[Ping, _Mapping]] = ...) -> None: ...

class AckControlRequest(_message.Message):
    __slots__ = ("worker_id", "event_id", "success", "error", "trace")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    event_id: str
    success: bool
    error: str
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., event_id: _Optional[str] = ..., success: bool = ..., error: _Optional[str] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class AckControlResponse(_message.Message):
    __slots__ = ("received",)
    RECEIVED_FIELD_NUMBER: _ClassVar[int]
    received: bool
    def __init__(self, received: bool = ...) -> None: ...

class TaskCancel(_message.Message):
    __slots__ = ("task_id", "run_id", "reason")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    run_id: str
    reason: str
    def __init__(self, task_id: _Optional[str] = ..., run_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

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

class RuntimeControl(_message.Message):
    __slots__ = ("request_id", "action", "params", "action_typed")
    class ParamsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    PARAMS_FIELD_NUMBER: _ClassVar[int]
    ACTION_TYPED_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    action: str
    params: _containers.ScalarMap[str, str]
    action_typed: RuntimeAction
    def __init__(self, request_id: _Optional[str] = ..., action: _Optional[str] = ..., params: _Optional[_Mapping[str, str]] = ..., action_typed: _Optional[_Union[RuntimeAction, _Mapping]] = ...) -> None: ...

class RuntimeAction(_message.Message):
    __slots__ = ("generic",)
    GENERIC_FIELD_NUMBER: _ClassVar[int]
    generic: GenericAction
    def __init__(self, generic: _Optional[_Union[GenericAction, _Mapping]] = ...) -> None: ...

class GenericAction(_message.Message):
    __slots__ = ("name", "args")
    class ArgsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    NAME_FIELD_NUMBER: _ClassVar[int]
    ARGS_FIELD_NUMBER: _ClassVar[int]
    name: str
    args: _containers.ScalarMap[str, str]
    def __init__(self, name: _Optional[str] = ..., args: _Optional[_Mapping[str, str]] = ...) -> None: ...

class Ping(_message.Message):
    __slots__ = ("timestamp",)
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    timestamp: _common_pb2.Timestamp
    def __init__(self, timestamp: _Optional[_Union[_common_pb2.Timestamp, _Mapping]] = ...) -> None: ...
