"""Spider data sink 抽象。

**为什么要抽这层**：Scrapy 子进程只能写本地 spool 或走独立 Gateway；
Worker 不允许直连 Redis 写 SpiderData，生产 Direct 模式由父进程可信控制面代写
``DataService.StreamSpiderData`` 由 gateway 转 Redis。两条路径的 wire
frame 完全一致（gateway handler 里字段名/顺序/类型和 direct 模式的 xadd
逐字节对齐），所以 pipeline 只要拿到一个"sink"接口 push 即可。

Rule/Spider Worker 使用 ``spool`` 模式，子进程只写本地文件，由 Worker 主进程
通过已认证 transport 转发。``gateway`` 保留给独立部署场景。

env 变量：
- ``ANTCODE_SPIDER_SINK_MODE`` = ``spool`` | ``redis`` | ``gateway``
- spool 模式：走 ``ANTCODE_SPIDER_SPOOL_PATH``
- redis 模式：已停用，选择后明确报错
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
    if mode == "spool":
        from antcode_scrapy.sinks.spool_sink import SpoolSpiderDataSink

        path = os.environ.get("ANTCODE_SPIDER_SPOOL_PATH", "").strip()
        return SpoolSpiderDataSink(path) if path else None
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
        # P1-01：优先 API key 认证（正常凭据链），Bearer token 作为回退。
        api_key = os.environ.get("ANTCODE_SPIDER_GATEWAY_API_KEY", "").strip()
        token = os.environ.get("ANTCODE_SPIDER_GATEWAY_AUTH_TOKEN", "").strip()
        return GatewaySpiderDataSink(
            endpoint=endpoint,
            secure=secure,
            token=token,
            api_key=api_key,
        )

    if mode == "redis":
        raise RuntimeError("Spider Redis 直写模式已停用；请使用 spool 或 gateway 可信写入通道")
    raise ValueError(f"未知 Spider sink mode: {mode}")


__all__ = ["SpiderDataSink", "create_sink"]
