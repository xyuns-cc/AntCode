"""Bounded bidirectional relay shared by both sides of the Rule bridge."""

from __future__ import annotations

import contextlib
import selectors
import socket
import time
from typing import cast

RULE_BRIDGE_CONNECT_TIMEOUT_SECONDS = 15
RULE_BRIDGE_IDLE_TIMEOUT_SECONDS = 60
_RELAY_BUFFER_BYTES = 64 * 1024
_RELAY_POLL_SECONDS = 0.1


def relay_bidirectional_bounded(
    left: socket.socket,
    right: socket.socket,
    *,
    max_duration_seconds: int,
) -> None:
    if type(max_duration_seconds) is not int or max_duration_seconds <= 0:
        raise ValueError("Rule egress relay 总时长上限必须是正整数")
    selector = selectors.DefaultSelector()
    selector.register(left, selectors.EVENT_READ, right)
    selector.register(right, selectors.EVENT_READ, left)
    idle_deadline = time.monotonic() + RULE_BRIDGE_IDLE_TIMEOUT_SECONDS
    total_deadline = time.monotonic() + max_duration_seconds
    with selector:
        while selector.get_map():
            remaining = min(idle_deadline, total_deadline) - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Rule egress bridge I/O idle/total timeout")
            events = selector.select(min(_RELAY_POLL_SECONDS, remaining))
            for key, _event_mask in events:
                source = cast(socket.socket, key.fileobj)
                destination = cast(socket.socket, key.data)
                if _relay_once(selector, source, destination):
                    idle_deadline = time.monotonic() + RULE_BRIDGE_IDLE_TIMEOUT_SECONDS


def _relay_once(
    selector: selectors.BaseSelector,
    source: socket.socket,
    destination: socket.socket,
) -> bool:
    data = source.recv(_RELAY_BUFFER_BYTES)
    if data:
        destination.sendall(data)
        return True
    with contextlib.suppress(Exception):
        selector.unregister(source)
    with contextlib.suppress(OSError):
        destination.shutdown(socket.SHUT_WR)
    return False


__all__ = [
    "RULE_BRIDGE_CONNECT_TIMEOUT_SECONDS",
    "RULE_BRIDGE_IDLE_TIMEOUT_SECONDS",
    "relay_bidirectional_bounded",
]
