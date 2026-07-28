"""Removed Direct Redis Spider reporter compatibility shim."""

from __future__ import annotations

from typing import Any


class RedisDataReporter:
    """Fail-loud shim for the removed untrusted Redis write path."""

    def __init__(
        self,
        redis_client: Any,
        keys: Any,
        *,
        run_id: str,
        project_id: str,
        spider_name: str,
        batch_size: int = 50,
        flush_interval: float = 5.0,
        ttl_seconds: int = 0,
        stream_max_len: int = 0,
    ) -> None:
        del redis_client, keys, run_id, project_id, spider_name
        del batch_size, flush_interval, ttl_seconds, stream_max_len
        raise RuntimeError("Direct Redis Spider reporter 已停用；请通过 Worker transport 的可信控制面上报")


__all__ = ["RedisDataReporter"]
