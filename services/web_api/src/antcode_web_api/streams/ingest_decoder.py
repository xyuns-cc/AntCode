"""Redis ingest 日志消息解码与归一化。"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from antcode_contracts import data_pb2
from antcode_core.infrastructure.redis import decode_stream_payload

from antcode_web_api.streams.sse import normalize_sequence


def decode_batch(
    fields: dict[Any, Any],
    run_id_filter: str | None = None,
) -> list[dict[str, Any]]:
    """解码一个 stream 消息，支持 protobuf LogBatch 和旧 JSON。"""
    batch = _parse_proto_batch(fields)
    if batch is not None:
        return [
            _proto_entry_to_dict(entry) for entry in batch.entries if not run_id_filter or entry.run_id == run_id_filter
        ]

    decoded = decode_stream_payload(fields)
    message_run_id = decoded.get("run_id") or ""
    if run_id_filter and message_run_id and message_run_id != run_id_filter:
        return []
    return [_json_entry_to_dict(decoded)]


def decode_batch_grouped(
    fields: dict[Any, Any],
    subscribed: set[str],
    *,
    event_id_prefix: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """批量解码并按 run_id 分组，仅保留已订阅执行。"""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    batch = _parse_proto_batch(fields)
    if batch is not None:
        for index, entry in enumerate(batch.entries):
            if entry.run_id in subscribed:
                normalized = _proto_entry_to_dict(entry)
                if event_id_prefix:
                    normalized["event_id"] = f"{event_id_prefix}:{index}"
                grouped[entry.run_id].append(normalized)
        return grouped

    decoded = decode_stream_payload(fields)
    run_id = decoded.get("run_id") or ""
    if run_id in subscribed:
        normalized = _json_entry_to_dict(decoded)
        if event_id_prefix:
            normalized["event_id"] = f"{event_id_prefix}:0"
        grouped[run_id].append(normalized)
    return grouped


def decode_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value) if value is not None else ""


def _parse_proto_batch(fields: dict[Any, Any]) -> data_pb2.LogBatch | None:
    if b"p" in fields:
        proto_raw = fields[b"p"]
    elif "p" in fields:
        proto_raw = fields["p"]
    else:
        return None
    if isinstance(proto_raw, str):
        proto_raw = proto_raw.encode("latin-1")
    batch = data_pb2.LogBatch()
    batch.ParseFromString(proto_raw)
    return batch


def _proto_entry_to_dict(entry: Any) -> dict[str, Any]:
    name = data_pb2.LogType.Name(entry.log_type)
    log_type = name.removeprefix("LOG_TYPE_").lower() if name.startswith("LOG_TYPE_") else name.lower()
    return {
        "log_type": log_type,
        "content": entry.content or "",
        "timestamp": _proto_timestamp(entry),
        "sequence": normalize_sequence(entry.sequence),
    }


def _proto_timestamp(entry: Any) -> str:
    if not entry.HasField("timestamp"):
        return ""
    seconds = entry.timestamp.seconds + entry.timestamp.nanos / 1e9
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _json_entry_to_dict(decoded: dict[Any, Any]) -> dict[str, Any]:
    return {
        "log_type": decode_value(decoded.get("log_type")) or "stdout",
        "content": decode_value(decoded.get("content")),
        "timestamp": decode_value(decoded.get("timestamp")),
        "sequence": normalize_sequence(decode_value(decoded.get("sequence"))),
    }


__all__ = ["decode_batch", "decode_batch_grouped", "decode_value"]
