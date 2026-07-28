"""Tombstone-fenced Gateway Spider metadata writes.

Lua 脚本统一复用 ``antcode_core.spider_write_fence``（Direct 同款实现：
lease/ownership/tombstone fence、TYPE 守卫、成员级 expiry index），此处只保留
Gateway 侧的薄封装。
"""

from __future__ import annotations

from typing import Any

from antcode_core.spider_write_fence import SpiderWriteIdentity, write_fenced_spider_meta


class SpiderMetaWriter:
    def __init__(self, redis_client: Any, *, ttl_seconds: int) -> None:
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds

    async def write(
        self,
        meta_key: str,
        tombstone_key: str,
        *,
        identity: SpiderWriteIdentity,
        marker_key: str,
        index_key: str,
        index_expiry_key: str,
        fields: dict[str, Any],
    ) -> None:
        await write_fenced_spider_meta(
            self._redis,
            meta_key,
            identity=identity,
            tombstone_key=tombstone_key,
            marker_key=marker_key,
            index_key=index_key,
            index_expiry_key=index_expiry_key,
            fields=fields,
            ttl_seconds=self._ttl_seconds,
        )


__all__ = ["SpiderMetaWriter"]
