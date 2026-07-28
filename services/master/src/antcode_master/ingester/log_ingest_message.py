"""Log-ingest message validation, mapping, and dead-letter serialization."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from antcode_contracts import data_pb2
from antcode_core.application.services.logs.log_sequence_allocator import LogSequenceAllocator
from antcode_core.application.services.logs.postgres_log_service import PostgresLogEntry

DLQ_MAX_ENTRIES = 10_000
DLQ_RAW_FIELD_MAX_CHARS = 2_048


class InvalidLogBatchError(ValueError):
    """A worker log batch contains invalid semantic data."""


def _idempotency_scope(log_batch: data_pb2.LogBatch, msg_id: str) -> str:
    """event_id 前缀：优先 batch_id（跨重发稳定），否则回退 redis msg_id。

    P1-GW-03: gateway 重试 / direct 重新 XADD 会产生新的 redis msg_id，
    只有 worker 侧内容确定性的 batch_id 才能让同一业务日志在响应丢失
    重发后命中 ``task_logs.event_id`` 唯一索引去重。msg_id 回退仅覆盖
    升级窗口内旧版 worker 的在途帧（含义与旧行为一致，不新增语义）。

    复审 P1-GW-05: batch_id 契约收紧为规范 sha256 hex（恰 64 字符）并
    重算内容哈希比对：event_id（``batch_id:index``）长度因此有硬上界，
    不会溢出 ``task_logs.event_id``（128）制造永久 PEL；同 batch_id 携带
    不同内容的批次被判坏帧进 DLQ，而不是被唯一索引静默吞掉发布旧行。
    """
    batch_id = log_batch.batch_id or ""
    if not batch_id:
        return msg_id
    from antcode_core.common.log_batch_hash import verify_batch_id

    if not verify_batch_id(log_batch.worker_id, log_batch.entries, batch_id):
        raise InvalidLogBatchError("batch_id 非法（要求 64 位 sha256 hex 且与内容哈希一致）")
    return batch_id


async def build_log_entries(
    log_batch: data_pb2.LogBatch,
    msg_id: str,
    allocator: LogSequenceAllocator,
) -> list[PostgresLogEntry]:
    prepared = _prepare_entries(log_batch)
    scope = _idempotency_scope(log_batch, msg_id)
    counts = Counter((entry.run_id, log_type) for _, entry, log_type in prepared)
    sequences = {key: await allocator.allocate(key[0], key[1], count) for key, count in counts.items()}
    offsets: defaultdict[tuple[str, str], int] = defaultdict(int)
    result: list[PostgresLogEntry] = []
    for index, entry, log_type in prepared:
        key = (entry.run_id, log_type)
        sequence = sequences[key][offsets[key]]
        offsets[key] += 1
        result.append(
            _to_postgres_entry(
                entry,
                worker_id=log_batch.worker_id,
                msg_id=scope,
                index=index,
                log_type=log_type,
                sequence=sequence,
            )
        )
    return result


# P1-SSE-01: 允许持久化的 Proto 日志类型白名单。LOG_TYPE_UNSPECIFIED（缺省
# 值 0）与协议外枚举值都不是合法业务日志：一旦按名字落库（如 "unspecified"）
# 会污染 task_logs 并破坏下游消费方的类型契约；未知整数值此前还会在
# ``LogType.Name`` 抛裸 ValueError，滞留 PEL 无限重试。两者统一走本模块
# 既有坏帧语义（InvalidLogBatchError -> DLQ），禁止静默改写成其他类型。
_ALLOWED_PROTO_LOG_TYPES = frozenset(
    {
        data_pb2.LogType.LOG_TYPE_STDOUT,
        data_pb2.LogType.LOG_TYPE_STDERR,
        data_pb2.LogType.LOG_TYPE_SYSTEM,
    }
)


def _require_entry_log_type(entry: Any) -> str:
    value = entry.log_type
    if value not in _ALLOWED_PROTO_LOG_TYPES:
        raise InvalidLogBatchError(f"log_type 非法（未指定或协议外枚举值）: {value}")
    return _proto_log_type_to_str(data_pb2.LogType.Name(value))


def _prepare_entries(log_batch: data_pb2.LogBatch) -> list[tuple[int, Any, str]]:
    prepared: list[tuple[int, Any, str]] = []
    for index, entry in enumerate(log_batch.entries):
        run_id = entry.run_id
        if not run_id or run_id != run_id.strip():
            raise InvalidLogBatchError("run_id 必须为非空规范字符串")
        prepared.append((index, entry, _require_entry_log_type(entry)))
    return prepared


def _to_postgres_entry(
    entry: Any,
    *,
    worker_id: str,
    msg_id: str,
    index: int,
    log_type: str,
    sequence: int,
) -> PostgresLogEntry:
    return PostgresLogEntry(
        run_id=entry.run_id,
        log_type=log_type,
        content=entry.content or "",
        timestamp=_ts_to_datetime(entry.timestamp),
        sequence=sequence,
        worker_id=worker_id or "",
        event_id=(f"{msg_id}:{index}" if msg_id else None),
    )


def require_event_ids(entries: list[PostgresLogEntry]) -> list[str]:
    event_ids: list[str] = []
    for entry in entries:
        if not entry.event_id:
            raise RuntimeError("待发布日志缺少稳定 event_id")
        event_ids.append(entry.event_id)
    return event_ids


async def dead_letter_bad_frame(
    client: Any,
    message: Any,
    *,
    source_stream: str,
    dlq_stream: str,
) -> None:
    entry: dict[str, str] = {
        "origin_stream": source_stream,
        "origin_msg_id": str(getattr(message, "msg_id", "")),
        "decode_error": str(getattr(message, "decode_error", "")),
        "dead_letter_at": datetime.now(UTC).isoformat(),
    }
    raw = getattr(message, "raw_fields", None)
    if isinstance(raw, dict):
        entry.update(_serialize_raw_fields(raw))
    await client.xadd(dlq_stream, entry, maxlen=DLQ_MAX_ENTRIES, approximate=True)


def _serialize_raw_fields(raw: dict[Any, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in raw.items():
        text_key = (
            key[:DLQ_RAW_FIELD_MAX_CHARS].decode("utf-8", errors="ignore") if isinstance(key, bytes) else str(key)
        )
        text_value = (
            value[:DLQ_RAW_FIELD_MAX_CHARS].decode("utf-8", errors="ignore") if isinstance(value, bytes) else str(value)
        )
        result[f"raw_{text_key}"] = text_value[:DLQ_RAW_FIELD_MAX_CHARS]
    return result


async def dead_letter_invalid_batch(
    client: Any,
    message: Any,
    error: Exception,
    *,
    source_stream: str,
    dlq_stream: str,
) -> None:
    batch = message.payload
    entry = {
        "origin_stream": source_stream,
        "origin_msg_id": str(message.msg_id),
        "decode_error": str(error),
        "worker_id": str(batch.worker_id or ""),
        "entry_count": str(len(batch.entries)),
        "dead_letter_at": datetime.now(UTC).isoformat(),
    }
    await client.xadd(dlq_stream, entry, maxlen=DLQ_MAX_ENTRIES, approximate=True)


def _proto_log_type_to_str(name: str) -> str:
    if name.startswith("LOG_TYPE_"):
        return name[len("LOG_TYPE_") :].lower()
    return name.lower()


def _ts_to_datetime(timestamp: Any) -> datetime | None:
    if timestamp is None:
        return None
    seconds = getattr(timestamp, "seconds", 0)
    nanos = getattr(timestamp, "nanos", 0)
    if seconds == 0 and nanos == 0:
        return None
    return datetime.fromtimestamp(seconds + nanos / 1e9, tz=UTC)


__all__ = [
    "InvalidLogBatchError",
    "build_log_entries",
    "dead_letter_bad_frame",
    "dead_letter_invalid_batch",
    "require_event_ids",
]
