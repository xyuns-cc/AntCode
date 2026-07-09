"""AntCode 内容去重 Pipeline —— 跨 run 按业务字段持久化去重。

场景：
- Scrapy 内置 ``RFPDupeFilter`` 只在**单次 run 内**按请求指纹（URL+method+body）
  去重；两次独立的 run 之间不共享。
- scrapy-redis 的 ``RFPDupeFilter``（``resume_enabled=true`` 时启用）能跨 run
  共享 URL 指纹，但仍然是 URL 层级——同一 URL 内容变了/不同 URL 内容重复
  都覆盖不到。
- **业务侧真正的去重**通常按 item 内容（如 title / id / detail_url）判定，这层
  必须在 pipeline 里做。

配置（``rule.dedup_config``，缺省不启用）：

    {
      "enabled": true,
      "fields": ["title", "url"],   # 拼串顺序即哈希顺序
      "scope": "project",           # project(跨 run 共享) | run(仅本次)
      "ttl_days": 30,               # 去重集 TTL；0=不过期
      "on_hit": "drop"              # drop(丢弃并计数) | log(仅记日志)
    }

Redis key:
- scope=project → ``{ns}:spider:dedup:{project_id}`` （SET）
- scope=run     → ``{ns}:spider:dedup:run:{run_id}``

**注意**：本 pipeline **在 AntCodeRedisPipeline 之前**执行（priority 较小），
命中即 DropItem，Redis stream 不会写入重复项。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from loguru import logger
from scrapy.exceptions import DropItem


class AntCodeDedupPipeline:
    """按 ``rule.dedup_config`` 做 item 级持久化去重。"""

    DEFAULT_TTL_DAYS = 30

    def __init__(self) -> None:
        self._redis = None
        self._enabled = False
        self._fields: list[str] = []
        self._scope: str = "project"
        self._on_hit: str = "drop"
        self._ttl_seconds: int = 0
        self._namespace: str = "antcode"
        self._project_id: str = ""
        self._run_id: str = ""
        self._hit_count: int = 0
        self._checked_count: int = 0

    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    async def open_spider(self, spider) -> None:
        """R1-P2-15 (审查报告): 用 redis.asyncio 避免阻塞 asyncio reactor。"""
        rule = getattr(spider, "rule", {}) or {}
        cfg = rule.get("dedup_config") or {}
        if not cfg.get("enabled"):
            return

        fields = cfg.get("fields") or []
        if not isinstance(fields, list) or not fields:
            logger.warning("dedup_config.enabled=True 但 fields 为空，去重跳过")
            return

        url = os.environ.get("ANTCODE_SPIDER_REDIS_URL", "")
        if not url:
            logger.warning("ANTCODE_SPIDER_REDIS_URL 未配置，去重跳过")
            return

        try:
            # T6-T1: 走统一 factory
            from antcode_core.infrastructure.redis.factory import (
                create_async_redis_client,
            )
        except ImportError as exc:  # pragma: no cover
            logger.warning(f"antcode_core.infrastructure.redis 不可用，去重跳过: {exc}")
            return

        self._enabled = True
        self._fields = [str(f) for f in fields]
        self._scope = str(cfg.get("scope") or "project").lower()
        self._on_hit = str(cfg.get("on_hit") or "drop").lower()
        ttl_days = int(cfg.get("ttl_days", self.DEFAULT_TTL_DAYS))
        self._ttl_seconds = ttl_days * 86400 if ttl_days > 0 else 0
        self._namespace = (
            os.environ.get("ANTCODE_SPIDER_REDIS_NAMESPACE", "").strip() or "antcode"
        )
        self._project_id = getattr(spider, "project_id", "") or ""
        self._run_id = getattr(spider, "run_id", "") or ""
        self._redis = create_async_redis_client(url, decode_responses=True)
        logger.info(
            f"AntCodeDedupPipeline 就绪: fields={self._fields} "
            f"scope={self._scope} on_hit={self._on_hit} ttl_days={ttl_days} "
            f"key={self._set_key()}"
        )

    async def process_item(self, item: dict[str, Any], spider):
        if not self._enabled or self._redis is None:
            return item

        # R1-P1-10 (审查报告): 老实现"先 SADD 后 xadd" —— 一旦 xadd 失败或
        # 进程崩溃，digest 已占位，重跑必被 DropItem 永久误杀。改成"两阶段"：
        # (1) 本 pipeline 只做 SISMEMBER 判存 + fail-open（Redis 挂了放行）；
        # (2) 把 digest 挂到 item 上，交给 RedisPipeline 在 xadd 成功**后**
        # 通过 spider._defer_dedup_commit 提交 SADD。
        # 这样重跑幂等且不会误杀。
        self._checked_count += 1
        key = self._set_key()
        digest = self._compute_digest(item)
        try:
            exists = bool(await self._redis.sismember(key, digest))
        except Exception as exc:
            logger.warning(f"dedup SISMEMBER 失败 (fail-open): {exc}")
            return item

        if exists:
            self._hit_count += 1
            if self._on_hit == "drop":
                raise DropItem(f"AntCodeDedup: 命中已抓 digest={digest[:12]}")
            logger.info(f"dedup hit but keep (on_hit=log): digest={digest[:12]}")
            return item

        # 未命中 → 把 digest + key + TTL 挂到 item，让 RedisPipeline 在 xadd 成功后 SADD
        item["_antcode_dedup"] = {
            "key": key,
            "digest": digest,
            "ttl_seconds": self._ttl_seconds,
        }
        return item

    async def close_spider(self, spider) -> None:
        if not self._enabled:
            return
        logger.info(
            f"AntCodeDedupPipeline 结束: checked={self._checked_count} "
            f"hit={self._hit_count} new={self._checked_count - self._hit_count}"
        )
        try:
            spider.crawler.stats.set_value("antcode/dedup_checked", self._checked_count)
            spider.crawler.stats.set_value("antcode/dedup_hit", self._hit_count)
        except Exception:
            pass
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _set_key(self) -> str:
        if self._scope == "run":
            return f"{self._namespace}:spider:dedup:run:{self._run_id}"
        return f"{self._namespace}:spider:dedup:{self._project_id}"

    def _compute_digest(self, item: dict[str, Any]) -> str:
        """按 fields 顺序拼串，sha256 得摘要。字段缺失记空字符串。"""
        parts: list[str] = []
        for field in self._fields:
            value = item.get(field)
            if value is None:
                parts.append("")
            elif isinstance(value, (str, int, float, bool)):
                parts.append(str(value))
            else:
                # 列表 / dict 等复合类型：JSON 稳定序列化
                parts.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
        raw = "\x1f".join(parts)  # ASCII US 分隔符，避免与内容冲突
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
