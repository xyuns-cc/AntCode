"""Consumer-owned Redis Stream PEL recovery."""

from __future__ import annotations

from collections import deque
from typing import Any

DEFAULT_RECOVERY_PAGE_SIZE = 64


class PendingMessageRecovery:
    """Drain one logical consumer's existing PEL before reading new entries."""

    def __init__(
        self,
        stream_key: str,
        group_name: str,
        *,
        page_size: int = DEFAULT_RECOVERY_PAGE_SIZE,
    ) -> None:
        if page_size < 1:
            raise ValueError("PEL recovery page_size 必须大于 0")
        self._stream_key = stream_key
        self._group_name = group_name
        self._page_size = page_size
        self.reset()

    @property
    def complete(self) -> bool:
        return self._complete

    def reset(self) -> None:
        self._cursor = "0-0"
        self._complete = False
        self._buffer: deque[tuple[str, dict[Any, Any]]] = deque()

    async def poll(self, redis: Any, consumer_name: str) -> tuple[str, dict[Any, Any]] | None:
        if self._buffer:
            return self._buffer.popleft()
        if self._complete:
            return None
        results = await redis.xreadgroup(
            groupname=self._group_name,
            consumername=consumer_name,
            streams={self._stream_key: self._cursor},
            count=self._page_size,
        )
        messages = results[0][1] if results else []
        if not messages:
            self._complete = True
            return None
        self._cursor = _text(messages[-1][0])
        self._buffer.extend((_text(message_id), fields) for message_id, fields in messages)
        return self._buffer.popleft()


class PendingChannelRecovery:
    """Drain multiple stream/group channels for one logical consumer."""

    def __init__(self, channels: tuple[Any, ...], *, page_size: int = DEFAULT_RECOVERY_PAGE_SIZE) -> None:
        if page_size < 1:
            raise ValueError("PEL recovery page_size 必须大于 0")
        self._channels = channels
        self._page_size = page_size
        self.reset()

    @property
    def complete(self) -> bool:
        return self._channel_index >= len(self._channels)

    def reset(self) -> None:
        self._channel_index = 0
        self._cursor = "0-0"
        self._buffer: deque[tuple[str, str, dict[Any, Any]]] = deque()

    async def poll(self, redis: Any, consumer_name: str) -> tuple[str, str, dict[Any, Any]] | None:
        if self._buffer:
            return self._buffer.popleft()
        while not self.complete:
            channel = self._channels[self._channel_index]
            results = await redis.xreadgroup(
                groupname=channel.group,
                consumername=consumer_name,
                streams={channel.stream_key: self._cursor},
                count=self._page_size,
            )
            messages = results[0][1] if results else []
            if not messages:
                self._channel_index += 1
                self._cursor = "0-0"
                continue
            self._cursor = _text(messages[-1][0])
            self._buffer.extend((channel.stream_key, _text(message_id), fields) for message_id, fields in messages)
            return self._buffer.popleft()
        return None


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)
