from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class Timestamp(_message.Message):
    __slots__ = ("seconds", "nanos")
    SECONDS_FIELD_NUMBER: _ClassVar[int]
    NANOS_FIELD_NUMBER: _ClassVar[int]
    seconds: int
    nanos: int
    def __init__(self, seconds: _Optional[int] = ..., nanos: _Optional[int] = ...) -> None: ...

class Metrics(_message.Message):
    __slots__ = ("cpu", "memory", "disk", "running_tasks", "max_concurrent_tasks", "task_count")
    CPU_FIELD_NUMBER: _ClassVar[int]
    MEMORY_FIELD_NUMBER: _ClassVar[int]
    DISK_FIELD_NUMBER: _ClassVar[int]
    RUNNING_TASKS_FIELD_NUMBER: _ClassVar[int]
    MAX_CONCURRENT_TASKS_FIELD_NUMBER: _ClassVar[int]
    TASK_COUNT_FIELD_NUMBER: _ClassVar[int]
    cpu: float
    memory: float
    disk: float
    running_tasks: int
    max_concurrent_tasks: int
    task_count: int
    def __init__(self, cpu: _Optional[float] = ..., memory: _Optional[float] = ..., disk: _Optional[float] = ..., running_tasks: _Optional[int] = ..., max_concurrent_tasks: _Optional[int] = ..., task_count: _Optional[int] = ...) -> None: ...

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
