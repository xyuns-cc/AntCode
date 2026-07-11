"""T6-T3b: Spider data sink 抽象。

**为什么要抽这层**：direct 模式下 Scrapy pipeline 直连 Redis 写
``spider:data:{run_id}``；gateway 模式下 worker 不允许直连 Redis，得走
``DataService.StreamSpiderData`` 由 gateway 转 Redis。两条路径的 wire
frame 完全一致（gateway handler 里字段名/顺序/类型和 direct 模式的 xadd
逐字节对齐），所以 pipeline 只要拿到一个"sink"接口 push 即可。

env 变量：
- ``ANTCODE_SPIDER_SINK_MODE`` = ``redis``（默认）| ``gateway``
- redis 模式：走 ``ANTCODE_SPIDER_REDIS_URL`` （不变）
- gateway 模式：走 ``ANTCODE_SPIDER_GATEWAY_ENDPOINT`` +
  ``ANTCODE_SPIDER_GATEWAY_AUTH_TOKEN``（可选）+
  ``ANTCODE_SPIDER_GATEWAY_SECURE=1``（可选，走 TLS）
"""

from __future__ import annotations

import os
from typing import Protocol


class SpiderDataSink(Protocol):
    """spider data 落地抽象。all-async。"""

    async def open(
        self,
        *,
        run_id: str,
        project_id: str,
        spider_name: str,
        namespace: str,
    ) -> None: ...

    async def write_item(
        self,
        *,
        item_id: str,
        item_type: str,
        data_json: str,
        url: str,
        timestamp: str,
        sequence: int,
    ) -> bool | tuple[bool, int]: ...

    async def write_meta(self, fields: dict[str, str]) -> None: ...

    async def close(self, final_meta: dict[str, str] | None = None) -> None | tuple[bool, int]: ...


def create_sink() -> SpiderDataSink | None:
    """按 env 装配 sink。缺配置时返回 None（pipeline 会 disable）。"""
    mode = os.environ.get("ANTCODE_SPIDER_SINK_MODE", "redis").strip().lower()
    if mode == "gateway":
        from antcode_scrapy.sinks.gateway_sink import GatewaySpiderDataSink

        endpoint = os.environ.get("ANTCODE_SPIDER_GATEWAY_ENDPOINT", "").strip()
        if not endpoint:
            return None
        secure = os.environ.get("ANTCODE_SPIDER_GATEWAY_SECURE", "").strip() in (
            "1",
            "true",
            "yes",
        )
        token = os.environ.get("ANTCODE_SPIDER_GATEWAY_AUTH_TOKEN", "").strip()
        return GatewaySpiderDataSink(endpoint=endpoint, secure=secure, token=token)

    # 默认 redis
    from antcode_scrapy.sinks.redis_sink import RedisSpiderDataSink

    url = os.environ.get("ANTCODE_SPIDER_REDIS_URL", "").strip()
    if not url:
        return None
    return RedisSpiderDataSink(url=url)


__all__ = ["SpiderDataSink", "create_sink"]
