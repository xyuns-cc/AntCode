"""Value objects shared by Direct Redis runtime-control modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ControlChannel:
    stream_key: str
    group: str


@dataclass(frozen=True)
class ControlSource:
    channel: ControlChannel
    message_id: str
    consumer_name: str


@dataclass(frozen=True)
class RuntimeControlResult:
    request_id: str
    reply_stream: str
    success: bool
    data: Any
    error: str
