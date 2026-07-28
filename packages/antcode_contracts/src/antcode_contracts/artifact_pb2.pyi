from . import common_pb2 as _common_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SourceBundleDownloadRequest(_message.Message):
    __slots__ = ("worker_id", "lease_id", "run_id", "project_id", "sha256", "size_bytes", "trace")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    SIZE_BYTES_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    lease_id: str
    run_id: str
    project_id: str
    sha256: str
    size_bytes: int
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., lease_id: _Optional[str] = ..., run_id: _Optional[str] = ..., project_id: _Optional[str] = ..., sha256: _Optional[str] = ..., size_bytes: _Optional[int] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class ArtifactChunk(_message.Message):
    __slots__ = ("offset", "data")
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    offset: int
    data: bytes
    def __init__(self, offset: _Optional[int] = ..., data: _Optional[bytes] = ...) -> None: ...

class TaskArtifactMetadata(_message.Message):
    __slots__ = ("worker_id", "lease_id", "run_id", "name", "media_type", "sha256", "size_bytes", "trace")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    MEDIA_TYPE_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    SIZE_BYTES_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    lease_id: str
    run_id: str
    name: str
    media_type: str
    sha256: str
    size_bytes: int
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., lease_id: _Optional[str] = ..., run_id: _Optional[str] = ..., name: _Optional[str] = ..., media_type: _Optional[str] = ..., sha256: _Optional[str] = ..., size_bytes: _Optional[int] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class ArtifactUploadFrame(_message.Message):
    __slots__ = ("metadata", "chunk")
    METADATA_FIELD_NUMBER: _ClassVar[int]
    CHUNK_FIELD_NUMBER: _ClassVar[int]
    metadata: TaskArtifactMetadata
    chunk: ArtifactChunk
    def __init__(self, metadata: _Optional[_Union[TaskArtifactMetadata, _Mapping]] = ..., chunk: _Optional[_Union[ArtifactChunk, _Mapping]] = ...) -> None: ...

class ArtifactUploadResponse(_message.Message):
    __slots__ = ("uri", "sha256", "size_bytes", "media_type")
    URI_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    SIZE_BYTES_FIELD_NUMBER: _ClassVar[int]
    MEDIA_TYPE_FIELD_NUMBER: _ClassVar[int]
    uri: str
    sha256: str
    size_bytes: int
    media_type: str
    def __init__(self, uri: _Optional[str] = ..., sha256: _Optional[str] = ..., size_bytes: _Optional[int] = ..., media_type: _Optional[str] = ...) -> None: ...

class RunOwnershipClaimRequest(_message.Message):
    __slots__ = ("worker_id", "lease_id", "run_id", "ttl_ms", "trace")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    TTL_MS_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    lease_id: str
    run_id: str
    ttl_ms: int
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., lease_id: _Optional[str] = ..., run_id: _Optional[str] = ..., ttl_ms: _Optional[int] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class RunOwnershipClaimResponse(_message.Message):
    __slots__ = ("acquired",)
    ACQUIRED_FIELD_NUMBER: _ClassVar[int]
    acquired: bool
    def __init__(self, acquired: bool = ...) -> None: ...

class RunOwnershipRenewRequest(_message.Message):
    __slots__ = ("worker_id", "lease_id", "run_id", "ttl_ms", "trace")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    TTL_MS_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    lease_id: str
    run_id: str
    ttl_ms: int
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., lease_id: _Optional[str] = ..., run_id: _Optional[str] = ..., ttl_ms: _Optional[int] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class RunOwnershipRenewResponse(_message.Message):
    __slots__ = ("renewed",)
    RENEWED_FIELD_NUMBER: _ClassVar[int]
    renewed: bool
    def __init__(self, renewed: bool = ...) -> None: ...

class RunOwnershipReleaseRequest(_message.Message):
    __slots__ = ("worker_id", "lease_id", "run_id", "trace")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    TRACE_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    lease_id: str
    run_id: str
    trace: _common_pb2.TraceContext
    def __init__(self, worker_id: _Optional[str] = ..., lease_id: _Optional[str] = ..., run_id: _Optional[str] = ..., trace: _Optional[_Union[_common_pb2.TraceContext, _Mapping]] = ...) -> None: ...

class RunOwnershipReleaseResponse(_message.Message):
    __slots__ = ("released",)
    RELEASED_FIELD_NUMBER: _ClassVar[int]
    released: bool
    def __init__(self, released: bool = ...) -> None: ...
