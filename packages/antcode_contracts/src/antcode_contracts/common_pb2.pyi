from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Timestamp(_message.Message):
    __slots__ = ("seconds", "nanos")
    SECONDS_FIELD_NUMBER: _ClassVar[int]
    NANOS_FIELD_NUMBER: _ClassVar[int]
    seconds: int
    nanos: int
    def __init__(self, seconds: _Optional[int] = ..., nanos: _Optional[int] = ...) -> None: ...

class TraceContext(_message.Message):
    __slots__ = ("traceparent", "tracestate")
    TRACEPARENT_FIELD_NUMBER: _ClassVar[int]
    TRACESTATE_FIELD_NUMBER: _ClassVar[int]
    traceparent: str
    tracestate: str
    def __init__(self, traceparent: _Optional[str] = ..., tracestate: _Optional[str] = ...) -> None: ...

class SpiderStatsSummary(_message.Message):
    __slots__ = ("request_count", "response_count", "item_scraped_count", "error_count", "avg_latency_ms", "requests_per_minute", "status_codes", "domain_stats")
    class StatusCodesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: int
        value: int
        def __init__(self, key: _Optional[int] = ..., value: _Optional[int] = ...) -> None: ...
    REQUEST_COUNT_FIELD_NUMBER: _ClassVar[int]
    RESPONSE_COUNT_FIELD_NUMBER: _ClassVar[int]
    ITEM_SCRAPED_COUNT_FIELD_NUMBER: _ClassVar[int]
    ERROR_COUNT_FIELD_NUMBER: _ClassVar[int]
    AVG_LATENCY_MS_FIELD_NUMBER: _ClassVar[int]
    REQUESTS_PER_MINUTE_FIELD_NUMBER: _ClassVar[int]
    STATUS_CODES_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_STATS_FIELD_NUMBER: _ClassVar[int]
    request_count: int
    response_count: int
    item_scraped_count: int
    error_count: int
    avg_latency_ms: float
    requests_per_minute: float
    status_codes: _containers.ScalarMap[int, int]
    domain_stats: _containers.RepeatedCompositeFieldContainer[SpiderDomainStats]
    def __init__(self, request_count: _Optional[int] = ..., response_count: _Optional[int] = ..., item_scraped_count: _Optional[int] = ..., error_count: _Optional[int] = ..., avg_latency_ms: _Optional[float] = ..., requests_per_minute: _Optional[float] = ..., status_codes: _Optional[_Mapping[int, int]] = ..., domain_stats: _Optional[_Iterable[_Union[SpiderDomainStats, _Mapping]]] = ...) -> None: ...

class SpiderDomainStats(_message.Message):
    __slots__ = ("domain", "request_count", "success_rate", "avg_latency_ms")
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    REQUEST_COUNT_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_RATE_FIELD_NUMBER: _ClassVar[int]
    AVG_LATENCY_MS_FIELD_NUMBER: _ClassVar[int]
    domain: str
    request_count: int
    success_rate: float
    avg_latency_ms: float
    def __init__(self, domain: _Optional[str] = ..., request_count: _Optional[int] = ..., success_rate: _Optional[float] = ..., avg_latency_ms: _Optional[float] = ...) -> None: ...

class Metrics(_message.Message):
    __slots__ = ("cpu", "memory", "disk", "running_tasks", "max_concurrent_tasks", "task_count", "project_count", "env_count", "spider_stats", "memory_total_bytes", "memory_used_bytes", "memory_available_bytes", "disk_total_bytes", "disk_used_bytes", "disk_free_bytes", "cpu_cores", "uptime_seconds", "queued_tasks", "task_memory_limit_mb", "task_cpu_time_limit_sec")
    CPU_FIELD_NUMBER: _ClassVar[int]
    MEMORY_FIELD_NUMBER: _ClassVar[int]
    DISK_FIELD_NUMBER: _ClassVar[int]
    RUNNING_TASKS_FIELD_NUMBER: _ClassVar[int]
    MAX_CONCURRENT_TASKS_FIELD_NUMBER: _ClassVar[int]
    TASK_COUNT_FIELD_NUMBER: _ClassVar[int]
    PROJECT_COUNT_FIELD_NUMBER: _ClassVar[int]
    ENV_COUNT_FIELD_NUMBER: _ClassVar[int]
    SPIDER_STATS_FIELD_NUMBER: _ClassVar[int]
    MEMORY_TOTAL_BYTES_FIELD_NUMBER: _ClassVar[int]
    MEMORY_USED_BYTES_FIELD_NUMBER: _ClassVar[int]
    MEMORY_AVAILABLE_BYTES_FIELD_NUMBER: _ClassVar[int]
    DISK_TOTAL_BYTES_FIELD_NUMBER: _ClassVar[int]
    DISK_USED_BYTES_FIELD_NUMBER: _ClassVar[int]
    DISK_FREE_BYTES_FIELD_NUMBER: _ClassVar[int]
    CPU_CORES_FIELD_NUMBER: _ClassVar[int]
    UPTIME_SECONDS_FIELD_NUMBER: _ClassVar[int]
    QUEUED_TASKS_FIELD_NUMBER: _ClassVar[int]
    TASK_MEMORY_LIMIT_MB_FIELD_NUMBER: _ClassVar[int]
    TASK_CPU_TIME_LIMIT_SEC_FIELD_NUMBER: _ClassVar[int]
    cpu: float
    memory: float
    disk: float
    running_tasks: int
    max_concurrent_tasks: int
    task_count: int
    project_count: int
    env_count: int
    spider_stats: SpiderStatsSummary
    memory_total_bytes: int
    memory_used_bytes: int
    memory_available_bytes: int
    disk_total_bytes: int
    disk_used_bytes: int
    disk_free_bytes: int
    cpu_cores: int
    uptime_seconds: int
    queued_tasks: int
    task_memory_limit_mb: int
    task_cpu_time_limit_sec: int
    def __init__(self, cpu: _Optional[float] = ..., memory: _Optional[float] = ..., disk: _Optional[float] = ..., running_tasks: _Optional[int] = ..., max_concurrent_tasks: _Optional[int] = ..., task_count: _Optional[int] = ..., project_count: _Optional[int] = ..., env_count: _Optional[int] = ..., spider_stats: _Optional[_Union[SpiderStatsSummary, _Mapping]] = ..., memory_total_bytes: _Optional[int] = ..., memory_used_bytes: _Optional[int] = ..., memory_available_bytes: _Optional[int] = ..., disk_total_bytes: _Optional[int] = ..., disk_used_bytes: _Optional[int] = ..., disk_free_bytes: _Optional[int] = ..., cpu_cores: _Optional[int] = ..., uptime_seconds: _Optional[int] = ..., queued_tasks: _Optional[int] = ..., task_memory_limit_mb: _Optional[int] = ..., task_cpu_time_limit_sec: _Optional[int] = ...) -> None: ...

class OSInfo(_message.Message):
    __slots__ = ("os_type", "os_version", "python_version", "machine_arch")
    OS_TYPE_FIELD_NUMBER: _ClassVar[int]
    OS_VERSION_FIELD_NUMBER: _ClassVar[int]
    PYTHON_VERSION_FIELD_NUMBER: _ClassVar[int]
    MACHINE_ARCH_FIELD_NUMBER: _ClassVar[int]
    os_type: str
    os_version: str
    python_version: str
    machine_arch: str
    def __init__(self, os_type: _Optional[str] = ..., os_version: _Optional[str] = ..., python_version: _Optional[str] = ..., machine_arch: _Optional[str] = ...) -> None: ...

class AuditEvent(_message.Message):
    __slots__ = ("event_type", "worker_id", "peer", "reason", "ts", "extra")
    class ExtraEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    EVENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    PEER_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    TS_FIELD_NUMBER: _ClassVar[int]
    EXTRA_FIELD_NUMBER: _ClassVar[int]
    event_type: str
    worker_id: str
    peer: str
    reason: str
    ts: Timestamp
    extra: _containers.ScalarMap[str, str]
    def __init__(self, event_type: _Optional[str] = ..., worker_id: _Optional[str] = ..., peer: _Optional[str] = ..., reason: _Optional[str] = ..., ts: _Optional[_Union[Timestamp, _Mapping]] = ..., extra: _Optional[_Mapping[str, str]] = ...) -> None: ...
