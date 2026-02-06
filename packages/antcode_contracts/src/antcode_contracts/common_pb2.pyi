from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Timestamp(_message.Message):
    __slots__ = ("seconds", "nanos")
    SECONDS_FIELD_NUMBER: _ClassVar[int]
    NANOS_FIELD_NUMBER: _ClassVar[int]
    seconds: int
    nanos: int
    def __init__(self, seconds: _Optional[int] = ..., nanos: _Optional[int] = ...) -> None: ...

class SpiderStatsSummary(_message.Message):
    __slots__ = ("request_count", "response_count", "item_scraped_count", "error_count", "avg_latency_ms", "requests_per_minute", "status_codes")
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
    request_count: int
    response_count: int
    item_scraped_count: int
    error_count: int
    avg_latency_ms: float
    requests_per_minute: float
    status_codes: _containers.ScalarMap[int, int]
    def __init__(self, request_count: _Optional[int] = ..., response_count: _Optional[int] = ..., item_scraped_count: _Optional[int] = ..., error_count: _Optional[int] = ..., avg_latency_ms: _Optional[float] = ..., requests_per_minute: _Optional[float] = ..., status_codes: _Optional[_Mapping[int, int]] = ...) -> None: ...

class Metrics(_message.Message):
    __slots__ = ("cpu", "memory", "disk", "running_tasks", "max_concurrent_tasks", "task_count", "project_count", "env_count", "spider_stats")
    CPU_FIELD_NUMBER: _ClassVar[int]
    MEMORY_FIELD_NUMBER: _ClassVar[int]
    DISK_FIELD_NUMBER: _ClassVar[int]
    RUNNING_TASKS_FIELD_NUMBER: _ClassVar[int]
    MAX_CONCURRENT_TASKS_FIELD_NUMBER: _ClassVar[int]
    TASK_COUNT_FIELD_NUMBER: _ClassVar[int]
    PROJECT_COUNT_FIELD_NUMBER: _ClassVar[int]
    ENV_COUNT_FIELD_NUMBER: _ClassVar[int]
    SPIDER_STATS_FIELD_NUMBER: _ClassVar[int]
    cpu: float
    memory: float
    disk: float
    running_tasks: int
    max_concurrent_tasks: int
    task_count: int
    project_count: int
    env_count: int
    spider_stats: SpiderStatsSummary
    def __init__(self, cpu: _Optional[float] = ..., memory: _Optional[float] = ..., disk: _Optional[float] = ..., running_tasks: _Optional[int] = ..., max_concurrent_tasks: _Optional[int] = ..., task_count: _Optional[int] = ..., project_count: _Optional[int] = ..., env_count: _Optional[int] = ..., spider_stats: _Optional[_Union[SpiderStatsSummary, _Mapping]] = ...) -> None: ...

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
