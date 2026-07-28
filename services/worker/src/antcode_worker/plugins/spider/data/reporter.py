"""Spider data reporter public API and factory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from antcode_worker.plugins.spider.data.gateway_reporter import (
    GatewayDataReporter,
    GatewaySpiderClient,
)
from antcode_worker.plugins.spider.data.redis_reporter import RedisDataReporter
from antcode_worker.plugins.spider.data.reporter_base import SpiderDataReporter

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from antcode_worker.transport.redis.keys import RedisKeys


async def create_data_reporter(
    mode: str,
    *,
    run_id: str,
    project_id: str,
    spider_name: str,
    redis_client: Redis | None = None,
    keys: RedisKeys | None = None,
    gateway_client: GatewaySpiderClient | None = None,
    **kwargs: Any,
) -> SpiderDataReporter:
    """Create and start a reporter for the selected transport mode."""
    if mode == "direct":
        raise RuntimeError("Direct Redis Spider reporter 已停用；请通过 Worker transport 的可信控制面上报")
    elif mode == "gateway":
        if gateway_client is None:
            raise ValueError("Gateway 模式需要 gateway_client")
        reporter = GatewayDataReporter(
            gateway_client,
            run_id=run_id,
            project_id=project_id,
            spider_name=spider_name,
            batch_size=kwargs.get("batch_size", 50),
            flush_interval=kwargs.get("flush_interval", 5.0),
        )
    else:
        raise ValueError(f"不支持的模式: {mode}")
    await reporter.start()
    return reporter


__all__ = [
    "GatewayDataReporter",
    "GatewaySpiderClient",
    "RedisDataReporter",
    "SpiderDataReporter",
    "create_data_reporter",
]
