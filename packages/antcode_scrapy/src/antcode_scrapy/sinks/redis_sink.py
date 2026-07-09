"""Redis sink —— direct 模式，把 items 直接 XADD 到 ``spider:data:{run_id}``。

这段本来在 redis_pipeline.py 里，T6-T3b 抽出来。行为、字段名、TTL 逻辑
和以前完全一致。
"""

from __future__ import annotations

import os
from datetime import datetime

from loguru import logger


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return v if v >= minimum else default


class RedisSpiderDataSink:
    """Redis 直写 sink。"""

    DEFAULT_STREAM_MAXLEN = 10000
    DEFAULT_META_TTL_SECONDS = 86400

    def __init__(self, url: str) -> None:
        self._url = url
        self._redis = None
        self._run_id = ""
        self._project_id = ""
        self._spider_name = ""
        self._namespace = "antcode"
        self._stream_maxlen = self.DEFAULT_STREAM_MAXLEN
        self._meta_ttl = self.DEFAULT_META_TTL_SECONDS
        self._first_write = True

    async def open(
        self,
        *,
        run_id: str,
        project_id: str,
        spider_name: str,
        namespace: str,
    ) -> None:
        # T6-T1: 走统一 factory，支持集群/哨兵
        from antcode_core.infrastructure.redis.factory import create_async_redis_client

        self._redis = create_async_redis_client(self._url, decode_responses=False)
        self._run_id = run_id
        self._project_id = project_id
        self._spider_name = spider_name
        self._namespace = namespace or "antcode"
        self._stream_maxlen = _env_int(
            "ANTCODE_SPIDER_STREAM_MAXLEN", self.DEFAULT_STREAM_MAXLEN
        )
        self._meta_ttl = _env_int(
            "ANTCODE_SPIDER_META_TTL_SECONDS", self.DEFAULT_META_TTL_SECONDS
        )
        # index: ZADD 项目→run 时间轴
        started_at = datetime.now()
        idx_key = f"{self._namespace}:spider:index:{project_id}"
        try:
            await self._redis.zadd(idx_key, {run_id: started_at.timestamp()})
            await self._redis.expire(idx_key, self._meta_ttl * 7)
        except Exception as exc:
            logger.warning(f"写 spider:index 失败: {exc}")

    def _stream_key(self) -> str:
        return f"{self._namespace}:spider:data:{self._run_id}"

    def _meta_key(self) -> str:
        return f"{self._namespace}:spider:meta:{self._run_id}"

    async def write_item(
        self,
        *,
        item_id: str,
        item_type: str,
        data_json: str,
        url: str,
        timestamp: str,
        sequence: int,
    ) -> bool:
        if self._redis is None:
            return False
        payload = {
            "item_id": item_id,
            "run_id": self._run_id,
            "project_id": self._project_id,
            "spider_name": self._spider_name,
            "item_type": item_type or "default",
            "data": data_json,
            "url": url,
            "timestamp": timestamp,
            "sequence": str(sequence),
        }
        try:
            await self._redis.xadd(
                self._stream_key(),
                payload,
                maxlen=self._stream_maxlen,
                approximate=True,
            )
            if self._first_write:
                self._first_write = False
                try:
                    await self._redis.expire(self._stream_key(), self._meta_ttl)
                except Exception:
                    pass
            return True
        except Exception as exc:
            logger.error(f"xadd spider:data 失败 seq={sequence}: {exc}")
            return False

    async def write_meta(self, fields: dict[str, str]) -> None:
        if self._redis is None or not fields:
            return
        base = {
            "run_id": self._run_id,
            "project_id": self._project_id,
            "spider_name": self._spider_name,
        }
        base.update({k: str(v) for k, v in fields.items() if v is not None})
        try:
            await self._redis.hset(self._meta_key(), mapping=base)
            await self._redis.expire(self._meta_key(), self._meta_ttl)
        except Exception as exc:
            logger.warning(f"写 spider:meta 失败: {exc}")

    async def close(self, final_meta: dict[str, str] | None = None) -> None:
        if final_meta:
            await self.write_meta(final_meta)
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
