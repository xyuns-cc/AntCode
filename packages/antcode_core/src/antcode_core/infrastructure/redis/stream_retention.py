"""Consumer-group-aware Redis Stream retention."""

from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


def _group_field(group: dict, name: str) -> Any:
    return group.get(name, group.get(name.encode()))


def _stream_id_key(stream_id: str) -> tuple[int, int]:
    milliseconds, sequence = stream_id.split("-", 1)
    return int(milliseconds), int(sequence)


async def trim_acknowledged_stream(
    redis: Any,
    stream_key: str,
    group_name: str | None = None,
) -> int:
    """Trim only entries older than every selected group's safe ACK boundary."""
    groups = await redis.xinfo_groups(stream_key)
    selected = []
    for group in groups or []:
        name = _text(_group_field(group, "name"))
        if group_name is None or name == group_name:
            selected.append((name, group))
    if not selected:
        return 0

    boundaries: list[str] = []
    for name, group in selected:
        pending = await redis.xpending(stream_key, name)
        pending_count = int(pending.get("pending", 0) or 0)
        if pending_count:
            minimum = pending.get("min")
            if not minimum:
                return 0
            boundaries.append(_text(minimum))
            continue
        last_delivered = _group_field(group, "last-delivered-id")
        if not last_delivered or _text(last_delivered) == "0-0":
            return 0
        boundaries.append(_text(last_delivered))

    min_id = min(boundaries, key=_stream_id_key)
    return int(
        await redis.xtrim(
            stream_key,
            minid=min_id,
            approximate=True,
        )
        or 0
    )


__all__ = ["trim_acknowledged_stream"]
