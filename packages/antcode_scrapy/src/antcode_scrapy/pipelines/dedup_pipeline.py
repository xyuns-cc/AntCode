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

**P2-01 修复**：老实现是 ``SISMEMBER`` 判存 + 由 RedisPipeline 在 xadd 成功
后再 ``SADD`` 提交占位（两阶段），并发同 digest 的两条 item 都过 SISMEMBER
→ 都写 stream，去重击穿；且 gateway 模式下 sink 没有 ``_redis`` 属性，SADD
静默失效。修复思路：直接用 ``SADD`` 返回值判存（1 = 新增，0 = 已存在），一
次原子操作合并"判存 + 占位"；dedup pipeline 自己持有独立 Redis 连接（
``ANTCODE_SPIDER_DEDUP_REDIS_URL`` / ``ANTCODE_SPIDER_REDIS_URL`` /
``REDIS_URL`` 顺序回退），gateway 模式也能正常工作。

**fail-open 策略**：Redis 连接失败或 SADD 抛异常时**放行 item**（不去重），
避免 Redis 抖动导致全量 drop。命中 DropItem（真去重）例外，直接抛出。
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
        self._redis: Any | None = None
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
        """初始化去重配置 + 独立 Redis 连接。

        P2-01: dedup pipeline 需要自己的 Redis 连接（不能借 sink 的），因为
        gateway 模式下 sink 不含 Redis。env 回退顺序：
        ``ANTCODE_SPIDER_DEDUP_REDIS_URL`` > ``ANTCODE_SPIDER_REDIS_URL``
        > ``REDIS_URL``。全都缺 → 去重跳过（不 fail 掉爬取）。
        """
        rule = getattr(spider, "rule", {}) or {}
        cfg = rule.get("dedup_config") or {}
        if not cfg.get("enabled"):
            return

        fields = cfg.get("fields") or []
        if not isinstance(fields, list) or not fields:
            logger.warning("dedup_config.enabled=True 但 fields 为空，去重跳过")
            return

        url = (
            os.environ.get("ANTCODE_SPIDER_DEDUP_REDIS_URL", "").strip()
            or os.environ.get("ANTCODE_SPIDER_REDIS_URL", "").strip()
            or os.environ.get("REDIS_URL", "").strip()
        )
        if not url:
            logger.warning(
                "dedup 需要的 Redis URL 未配置 "
                "(ANTCODE_SPIDER_DEDUP_REDIS_URL / ANTCODE_SPIDER_REDIS_URL / "
                "REDIS_URL 均缺失)，去重跳过"
            )
            return

        try:
            # T6-T1: 走统一 factory
            from antcode_core.infrastructure.redis.factory import (
                create_async_redis_client,
            )
        except ImportError as exc:  # pragma: no cover
            logger.warning(f"antcode_core.infrastructure.redis 不可用，去重跳过: {exc}")
            return

        self._fields = [str(f) for f in fields]
        self._scope = str(cfg.get("scope") or "project").lower()
        self._on_hit = str(cfg.get("on_hit") or "drop").lower()
        ttl_days = int(cfg.get("ttl_days", self.DEFAULT_TTL_DAYS))
        self._ttl_seconds = ttl_days * 86400 if ttl_days > 0 else 0
        # R2 seam-4 (接缝修复): ``ttl_days=0``（不过期）对 project 级去重集
        # 是合法语义（长期业务去重），但对 run 级等于每个 run 永久泄漏一个
        # Redis SET —— run_id 全局唯一不复用，run 结束后没有任何环节清理
        # ``{ns}:spider:dedup:run:{run_id}``。给 run 级强制兜底 TTL，防止
        # 高吞吐批次把 Redis 内存拖爆。
        if self._scope == "run" and self._ttl_seconds <= 0:
            self._ttl_seconds = self.DEFAULT_TTL_DAYS * 86400
            logger.info(f"dedup scope=run 且 ttl_days=0，应用兜底 TTL {self.DEFAULT_TTL_DAYS} 天防 Redis 泄漏")
        self._namespace = os.environ.get("ANTCODE_SPIDER_REDIS_NAMESPACE", "").strip() or "antcode"
        self._project_id = getattr(spider, "project_id", "") or ""
        self._run_id = getattr(spider, "run_id", "") or ""
        try:
            self._redis = create_async_redis_client(url, decode_responses=True)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"dedup Redis 客户端创建失败，去重跳过: {exc}")
            return

        self._enabled = True
        logger.info(
            f"AntCodeDedupPipeline 就绪: fields={self._fields} "
            f"scope={self._scope} on_hit={self._on_hit} ttl_days={ttl_days} "
            f"key={self._set_key()}"
        )

    async def process_item(self, item: dict[str, Any], spider):
        """P2-01: 用 SADD 返回值原子判存 + 占位。

        - ``SADD key digest`` 返回 1 = 新增（未命中，放行）
        - 返回 0 = 已存在（命中，按 ``on_hit`` 处理）
        - 一次 Redis round-trip 完成"判存 + 占位"，消除 check-then-act
          竞态：并发同 digest 的两条 item，只会有一条拿到 added=1。

        **fail-open**：SADD 抛异常时**放行 item**（不 dedup 优于 drop 所有
        item）。命中 DropItem 例外。
        """
        if not self._enabled or self._redis is None:
            return item

        digest = self._compute_digest(item)
        if not digest:
            return item

        self._checked_count += 1
        key = self._set_key()

        try:
            # SADD 返回新增的元素数：1 = 新增，0 = 已存在
            added = int(await self._redis.sadd(key, digest))
        except Exception as exc:
            # P2-01: Redis 抖动/网络异常时 fail-open —— 放行本条 item，
            # 避免整批 drop。真去重（added=0）走下面 DropItem，不吃这里。
            logger.warning(f"dedup SADD 失败，放行 item (fail-open): {exc}")
            return item

        if added == 0:
            # 已存在 → 命中
            self._hit_count += 1
            if self._on_hit == "drop":
                raise DropItem(f"AntCodeDedup: 命中已抓 digest={digest[:12]}")
            logger.info(f"dedup hit but keep (on_hit=log): digest={digest[:12]}")
            return item

        # 新增成功 → 顺手续 TTL（每次续，保证 project 级 SET 不会被卡死过期）
        if self._ttl_seconds > 0:
            try:
                await self._redis.expire(key, self._ttl_seconds)
            except Exception as exc:
                raise RuntimeError(f"dedup TTL 续期失败: key={key}") from exc

        return item

    async def close_spider(self, spider) -> None:
        if not self._enabled:
            return
        logger.info(
            f"AntCodeDedupPipeline 结束: checked={self._checked_count} "
            f"hit={self._hit_count} new={self._checked_count - self._hit_count}"
        )
        spider.crawler.stats.set_value("antcode/dedup_checked", self._checked_count)
        spider.crawler.stats.set_value("antcode/dedup_hit", self._hit_count)
        if self._redis is not None:
            await self._redis.aclose()

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
