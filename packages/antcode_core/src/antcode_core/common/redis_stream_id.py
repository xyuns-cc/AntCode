"""Strict Redis Stream ID parsing and ordering."""

MAX_STREAM_ID_COMPONENT = (1 << 64) - 1
MAX_STREAM_ID_LENGTH = 41
STREAM_ID_COMPONENT_COUNT = 2
STREAM_ID_MAX_SPLITS = 1


def parse_stream_id(value: str) -> tuple[int, int]:
    normalized = str(value)
    if len(normalized) > MAX_STREAM_ID_LENGTH:
        raise ValueError(f"非法 Redis Stream ID: {value!r}")
    parts = normalized.split("-", STREAM_ID_MAX_SPLITS)
    if len(parts) != STREAM_ID_COMPONENT_COUNT or not all(part.isdigit() for part in parts):
        raise ValueError(f"非法 Redis Stream ID: {value!r}")
    parsed = int(parts[0]), int(parts[1])
    if any(component > MAX_STREAM_ID_COMPONENT for component in parsed):
        raise ValueError(f"非法 Redis Stream ID: {value!r}")
    return parsed


def stream_id_not_after(message_id: str, cutoff_id: str) -> bool:
    return parse_stream_id(message_id) <= parse_stream_id(cutoff_id)


__all__ = ["MAX_STREAM_ID_LENGTH", "parse_stream_id", "stream_id_not_after"]
