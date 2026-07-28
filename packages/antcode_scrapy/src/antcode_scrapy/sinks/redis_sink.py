"""Removed Direct Redis Spider sink compatibility shim."""

from __future__ import annotations


class RedisSpiderDataSink:
    """Fail-loud shim for the removed untrusted Redis write path."""

    def __init__(self, url: str) -> None:
        del url

    async def open(
        self,
        *,
        run_id: str,
        project_id: str,
        spider_name: str,
        namespace: str,
    ) -> None:
        del run_id, project_id, spider_name, namespace
        raise RuntimeError("Spider Redis 直写模式已停用；请使用 spool 或 gateway 可信写入通道")


__all__ = ["RedisSpiderDataSink"]
