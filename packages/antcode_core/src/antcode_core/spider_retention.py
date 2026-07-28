"""SpiderData Redis retention 的共享契约。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SpiderRetention:
    """0 表示不裁剪、不自动过期；正数表示显式 retention。"""

    stream_max_len: int = 0
    ttl_seconds: int = 0

    def __post_init__(self) -> None:
        _validate_value("stream_max_len", self.stream_max_len)
        _validate_value("ttl_seconds", self.ttl_seconds)

    @classmethod
    def from_env(
        cls,
        *,
        stream_max_len_env: str,
        ttl_seconds_env: str,
    ) -> SpiderRetention:
        return cls(
            stream_max_len=_read_env(stream_max_len_env),
            ttl_seconds=_read_env(ttl_seconds_env),
        )


def _read_env(name: str) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return 0
    if not raw.strip():
        raise ValueError(f"{name} 必须是非负整数，0 表示无限保留: {raw!r}")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是非负整数，0 表示无限保留: {raw!r}") from exc
    _validate_value(name, value)
    return value


def _validate_value(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} 必须是非负整数，0 表示无限保留: {value!r}")


__all__ = ["SpiderRetention"]
