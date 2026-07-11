"""AntCode 代理池 Scrapy Downloader Middleware。

Redis 数据结构（平台级共享，rule / code(Scrapy) / render 三类插件共用）：

- ``{namespace}:proxy:pool`` — ZSET，member=proxy_url，score=健康分（越大越优）
- ``{namespace}:proxy:cooldown:{proxy}`` — SET NX + TTL 的黑名单，命中不选

策略：
- ``rule.proxy_config.enabled`` = True 时启用（缺省关闭，向后兼容）
- ``rule.proxy_config.strategy`` = ``rotate``（每 request 换）| ``sticky``（同会话保持）
- 请求失败（连接异常 / 5xx / 429）→ 降分 + 写 cooldown（默认 60s）
- 请求成功（2xx）→ 升分

**说明**：本 middleware 只负责"从 Redis 池里挑一个 proxy 挂到
request.meta.proxy"。代理池的**补充与探活**是平台级 loop（master 侧
``proxy_health_loop``，S3 后续接线），不属于本包。
"""

from __future__ import annotations

import os

from loguru import logger


class AntCodeProxyMiddleware:
    """从 Redis 代理池挑代理挂到 request.meta['proxy']。"""

    COOLDOWN_TTL = 60  # 秒
    SCORE_SUCCESS = 1
    SCORE_FAILURE = 5  # 失败降分幅度更大

    def __init__(
        self,
        redis_url: str,
        namespace: str = "antcode",
        strategy: str = "rotate",
    ):
        self.redis_url = redis_url
        self.namespace = namespace
        self.strategy = strategy
        self._redis = None
        self._sticky_proxy: str | None = None

    @classmethod
    def from_crawler(cls, crawler):
        rule = getattr(crawler.spider, "rule", {}) if crawler.spider else {}
        proxy_cfg = (rule or {}).get("proxy_config") or {}
        if not proxy_cfg.get("enabled"):
            # 不启用时抛 NotConfigured 让 Scrapy 跳过挂载
            from scrapy.exceptions import NotConfigured

            raise NotConfigured("rule.proxy_config.enabled=False")
        url = os.environ.get("ANTCODE_SPIDER_REDIS_URL", "")
        if not url:
            from scrapy.exceptions import NotConfigured

            raise NotConfigured("ANTCODE_SPIDER_REDIS_URL 未配置，无法启用代理池")
        return cls(
            redis_url=url,
            namespace=os.environ.get("ANTCODE_SPIDER_REDIS_NAMESPACE", "") or "antcode",
            strategy=str(proxy_cfg.get("strategy") or "rotate").lower(),
        )

    def _get_redis(self):
        if self._redis is None:
            # T6-T1: 走统一 sync factory，支持 cluster/sentinel URL scheme
            from antcode_core.infrastructure.redis.factory import (
                create_sync_redis_client,
            )

            self._redis = create_sync_redis_client(self.redis_url, decode_responses=True)
        return self._redis

    def _pool_key(self) -> str:
        return f"{self.namespace}:proxy:pool"

    def _cooldown_key(self, proxy: str) -> str:
        # R1-P2-16 (审查报告): 代理凭证 (user:pass) 明文进 keyspace，是
        # 严重安全漏洞——Redis MONITOR / SLOWLOG / RDB 备份都会看到凭证。
        # 对整个 proxy URL 做 sha256 得固定长度 hex 作为 key 后缀，凭证
        # 只在 pool ZSET 的 member 里出现（member 本身访问受控 ACL）。
        import hashlib as _hashlib

        digest = _hashlib.sha256(proxy.encode("utf-8")).hexdigest()[:32]
        return f"{self.namespace}:proxy:cooldown:{digest}"

    def _pick(self) -> str | None:
        r = self._get_redis()
        # 拿分数最高的前 20 个（缓解热点集中）
        candidates = r.zrevrange(self._pool_key(), 0, 19, withscores=True) or []
        for member, _score in candidates:
            proxy = member
            if not r.exists(self._cooldown_key(proxy)):
                return proxy
        # R1-P2-17 (审查报告): 老实现全员冷却时 random.choice 直接返回
        # 冷却中代理——冷却机制在池小的时候形同虚设，被封代理仍被反复用。
        # 修复：全员冷却时返回 None，让请求走"无代理直连"或触发上游降级
        # (Scrapy 会走默认下载链)，配合日志告警让运维知道代理池全废。
        if candidates:
            logger.warning("proxy 池全员冷却中，本次请求不加代理")
        return None

    # ------------------------------------------------------------------
    # Scrapy hooks
    # ------------------------------------------------------------------
    def process_request(self, request, spider):
        # sticky：会话内保持同一 proxy
        if self.strategy == "sticky" and self._sticky_proxy:
            request.meta["proxy"] = self._sticky_proxy
            return None
        proxy = self._pick()
        if proxy:
            request.meta["proxy"] = proxy
            request.meta["_antcode_proxy_selected"] = proxy
            if self.strategy == "sticky":
                self._sticky_proxy = proxy
        return None

    def process_response(self, request, response, spider):
        proxy = request.meta.get("_antcode_proxy_selected")
        if not proxy:
            return response
        status = response.status
        try:
            r = self._get_redis()
            if 200 <= status < 300:
                r.zincrby(self._pool_key(), self.SCORE_SUCCESS, proxy)
            elif status in (403, 407, 429, 502, 503, 504):
                # R1-P2-17: 403 代理被目标站封（最常见）、407 代理需鉴权失败，
                # 老实现漏了这两个 → 被封代理长期占据高分位、反复被选中。
                r.zincrby(self._pool_key(), -self.SCORE_FAILURE, proxy)
                r.setex(self._cooldown_key(proxy), self.COOLDOWN_TTL, "1")
                if self.strategy == "sticky":
                    self._sticky_proxy = None  # 失效切换
        except Exception as exc:
            logger.warning(f"记录 proxy 结果失败: {exc}")
        return response

    def process_exception(self, request, exception, spider):
        proxy = request.meta.get("_antcode_proxy_selected")
        if not proxy:
            return None
        try:
            r = self._get_redis()
            r.zincrby(self._pool_key(), -self.SCORE_FAILURE, proxy)
            r.setex(self._cooldown_key(proxy), self.COOLDOWN_TTL, "1")
            if self.strategy == "sticky":
                self._sticky_proxy = None
        except Exception as exc:
            logger.warning(f"记录 proxy 异常失败: {exc}")
        return None
