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

用 ``SADD`` 返回值判存（1 = 新增，0 = 已存在），一次原子操作合并"判存 + 占位"，
避免 check-then-act 竞态。连接自持（不借 sink 的），因为 sink 不含 Redis。

**spool 模式不可用**：Rule 子进程的环境白名单（``rule_policy.RULE_PLUGIN_ENV_VARS``）
不含任何 Redis URL，且沙箱 ``allow_network=False``。该模式下 ``open_spider``
直接报错——静默跳过会让用户以为去重生效、重复数据却照常入库。

**fail-open 仅限 SADD 运行时异常**：放行该条 item，避免 Redis 抖动导致全量
drop。命中 DropItem（真去重）例外，直接抛出。配置错误与连接建立失败一律报错。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from loguru import logger
from scrapy.exceptions import DropItem


def _digest_part(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    # 列表 / dict 等复合类型：JSON 稳定序列化
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


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

        env 回退顺序：``ANTCODE_SPIDER_DEDUP_REDIS_URL`` >
        ``ANTCODE_SPIDER_REDIS_URL`` > ``REDIS_URL``。
        """
        rule = getattr(spider, "rule", {}) or {}
        cfg = rule.get("dedup_config") or {}
        if not cfg.get("enabled"):
            return

        # 以下每一条都是"用户要了去重但我们给不了"。静默跳过会让重复数据照常
        # 入库且只留一行 warning，用户无从发现；一律报错中止。
        fields = cfg.get("fields") or []
        if not isinstance(fields, list) or not fields:
            raise RuntimeError("dedup_config.enabled=True 但 fields 为空；请配置参与去重的字段名")

        url = (
            os.environ.get("ANTCODE_SPIDER_DEDUP_REDIS_URL", "").strip()
            or os.environ.get("ANTCODE_SPIDER_REDIS_URL", "").strip()
            or os.environ.get("REDIS_URL", "").strip()
        )
        if not url:
            raise RuntimeError(
                "dedup_config.enabled=True 但没有可用的 Redis URL "
                "(ANTCODE_SPIDER_DEDUP_REDIS_URL / ANTCODE_SPIDER_REDIS_URL / "
                "REDIS_URL 均缺失)。spool 模式不向 Rule 子进程下发 Redis 凭据，"
                "该模式下请关闭 dedup_config。"
            )

        # T6-T1: 走统一 factory
        from antcode_core.infrastructure.redis.factory import create_async_redis_client

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
        self._redis = create_async_redis_client(url, decode_responses=True)

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
        """按 fields 顺序拼串，sha256 得摘要。

        item 的键是各条 extraction_rule 的 ``desc``，而 ``dedup_config.fields``
        是前端自由输入的标签，两者对不上是常见配置错误。此时每条 item 的
        摘要都只由空串拼成、完全相同——第一条之后的整批数据会被当成重复
        全部 DropItem。必须在第一条就报错，不能把它当正常去重跑下去。
        """
        if all(item.get(field) is None for field in self._fields):
            raise ValueError(
                f"dedup_config.fields={self._fields} 在 item 中一个都不存在"
                f"（item 实际字段: {sorted(item)}）。fields 必须与 extraction_rules "
                f"的 desc 一致，否则所有 item 摘要相同、会被整批丢弃。"
            )
        parts = [_digest_part(item.get(field)) for field in self._fields]
        raw = "\x1f".join(parts)  # ASCII US 分隔符，避免与内容冲突
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
