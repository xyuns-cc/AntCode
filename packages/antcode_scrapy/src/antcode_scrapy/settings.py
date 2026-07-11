"""按 rule 动态装配 Scrapy settings。

分层：
- 基础设置（并发、超时、日志、pipeline 注册）永远启用
- engine=playwright/render → 装 scrapy-playwright DownloadHandler + asyncio reactor
- engine=curl_cffi → 装 scrapy-impersonate（保留旧 spiderkit 的指纹能力）

resume_enabled（S3 加）→ scrapy-redis 调度器，实现断点续爬 + 单爬虫分布式。
"""

from __future__ import annotations

import os
from typing import Any


def build_settings(rule: dict[str, Any]) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "BOT_NAME": "antcode",
        # Scrapy 2.7+ 用 async def parse（我们的 UniversalRuleSpider.parse 为
        # 了 playwright evaluate 是 async 的）必须走 asyncio reactor；否则
        # spider 直接 finish 抓 0 页。所有 engine 统一走 asyncio。
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        # 关掉 robots.txt：AntCode 已在业务层控制（未来若需要，从 rule.task_config 读）
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS": int(rule.get("concurrent_requests") or 8),
        # request_delay 从毫秒转秒（对齐 ProjectRule.request_delay 语义）
        "DOWNLOAD_DELAY": (int(rule.get("request_delay") or 1000)) / 1000.0,
        "RANDOMIZE_DOWNLOAD_DELAY": True,
        "RETRY_TIMES": int(rule.get("retry_count") or 3),
        "DOWNLOAD_TIMEOUT": int(rule.get("timeout") or 30),
        # LOG_LEVEL → stdout → worker executor 捕获 → log:ingest → task_logs
        "LOG_LEVEL": os.environ.get("ANTCODE_SCRAPY_LOG_LEVEL", "INFO"),
        "TELNETCONSOLE_ENABLED": False,
        # 关闭 Feed/Log stats 到磁盘（我们要 stdout）
        "LOG_ENABLED": True,
        "LOG_STDOUT": False,
        # Pipeline：我们的 Redis pipeline
        # Pipeline 优先级：dedup 200 < redis 300 —— 去重命中 DropItem 后不会
        # 再写入 Redis stream；顺序不能颠倒。
        "ITEM_PIPELINES": {
            "antcode_scrapy.pipelines.dedup_pipeline.AntCodeDedupPipeline": 200,
            "antcode_scrapy.pipelines.redis_pipeline.AntCodeRedisPipeline": 300,
        },
        "DOWNLOADER_MIDDLEWARES": {},
        # 请求指纹 v2.7+ 默认，避免弃用警告
        "REQUEST_FINGERPRINTER_IMPLEMENTATION": "2.7",
    }

    engine = str(rule.get("engine") or "").lower()
    # R1-P1-13 (审查报告): pagination_config.method=js_click / infinite_scroll
    # 需要 Playwright；老实现 rule.engine 若不是 playwright，spider 侧
    # request meta 挂了 playwright=True 但 handler 未装 → playwright_page
    # 为 None，翻页整段跳过静默单页。这里强制把 engine 提升为 playwright。
    pagination_method = ((rule.get("pagination_config") or {}).get("method") or "").lower()
    if pagination_method in ("js_click", "infinite_scroll"):
        if engine not in ("playwright", "render"):
            # 自动提升 engine 到 playwright（用户配了 method=js_click 但没配
            # engine=playwright，两种情况都当他期望 JS 分页）
            engine = "playwright"

    # Playwright: 用 scrapy-playwright DownloadHandler + asyncio reactor
    if engine in ("playwright", "render"):
        settings.update(
            {
                "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
                "DOWNLOAD_HANDLERS": {
                    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
                    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
                },
                "PLAYWRIGHT_BROWSER_TYPE": os.environ.get("ANTCODE_PLAYWRIGHT_BROWSER", "chromium"),
                "PLAYWRIGHT_LAUNCH_OPTIONS": {"headless": True},
                # 上下文上限：与 worker memory_limit_mb 配合避免爆内存
                "PLAYWRIGHT_MAX_CONTEXTS": int(os.environ.get("ANTCODE_PLAYWRIGHT_MAX_CONTEXTS", "4")),
                "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": (int(rule.get("timeout") or 30) * 1000),
            }
        )

    # curl_cffi 引擎：走 scrapy-impersonate（保留 TLS/JA3 指纹伪装）
    elif engine == "curl_cffi":
        # R1-P1-11 (审查报告): scrapy-impersonate 不在 pyproject 依赖里
        # (venv 实测 `import scrapy_impersonate` 直接 ModuleNotFoundError)。
        # 之前任何 engine=curl_cffi 的规则一跑就在下载握手失败。
        # 修复：探测导入，缺失时明确报错让用户知道要装依赖 或 降级到 requests。
        try:
            import scrapy_impersonate  # noqa: F401

            # 装了就用
            settings["TWISTED_REACTOR"] = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
            settings["DOWNLOAD_HANDLERS"] = {
                "http": "scrapy_impersonate.ImpersonateDownloadHandler",
                "https": "scrapy_impersonate.ImpersonateDownloadHandler",
            }
        except ImportError:
            # 未装 → 明确报错让上游任务失败，而不是静默降级导致 "指纹伪装"
            # 没生效但用户不知道
            raise RuntimeError(
                "rule.engine=curl_cffi 需要安装 scrapy-impersonate："
                "pip install scrapy-impersonate。当前 antcode-scrapy 未声明"
                "该依赖，请在 worker 环境中显式安装或改用 engine=playwright/requests。"
            )

    # S3a: 代理池 — 仅当 rule.proxy_config.enabled=True 时挂载
    # 中间件自身会读 rule.proxy_config，配置放在 spider.rule 里，不需要把
    # 具体 proxy 写死到 settings。优先级 750 排在 Scrapy 内置 HttpProxy
    # (750) 之后但比多数用户中间件早（Scrapy 内置 HttpProxyMiddleware
    # 也是 750，这里显式设 749 更早）。
    proxy_cfg = rule.get("proxy_config") or {}
    if proxy_cfg.get("enabled"):
        settings["DOWNLOADER_MIDDLEWARES"] = {
            **settings.get("DOWNLOADER_MIDDLEWARES", {}),
            "antcode_scrapy.proxy.AntCodeProxyMiddleware": 749,
        }

    # S3b + R1-P1-12 (审查报告)：scrapy-redis 断点续爬 + 分布式
    # 老实现 `os.environ["ANTCODE_SPIDER_REDIS_URL"]` 硬索引，env 缺失
    # KeyError 裸崩；SCHEDULER_PERSIST=True + 固定 project 级 DUPEFILTER_KEY
    # 无 TTL —— 二次运行同 project 时**所有请求命中 dupefilter，抓 0 页
    # 假成功**（exit 0）。这里 fail-fast 缺失 env，且给 dupefilter 挂 TTL。
    if rule.get("resume_enabled"):
        redis_url = os.environ.get("ANTCODE_SPIDER_REDIS_URL") or ""
        if not redis_url:
            raise RuntimeError(
                "rule.resume_enabled=True 需要 ANTCODE_SPIDER_REDIS_URL，但环境变量为空。请检查 worker 侧 Redis 配置。"
            )
        project_id = os.environ.get("ANTCODE_SPIDER_PROJECT_ID", "") or "default"
        ns = os.environ.get("ANTCODE_SPIDER_REDIS_NAMESPACE", "") or "antcode"
        # dupefilter key 带 run_id 后缀（一次 run 内保留、跨 run 不共享）——
        # scrapy-redis 老实现无 TTL，二次跑同 project 静默 0 items。若真需要
        # 跨 run 共享，用 rule.dedup_config 走内容级去重。
        run_id = os.environ.get("ANTCODE_SPIDER_RUN_ID", "") or "0"
        settings.update(
            {
                "SCHEDULER": "scrapy_redis.scheduler.Scheduler",
                "SCHEDULER_PERSIST": False,  # run 结束清空调度器队列，避免陈旧
                "DUPEFILTER_CLASS": "scrapy_redis.dupefilter.RFPDupeFilter",
                "SCHEDULER_QUEUE_KEY": f"{ns}:scrapy:{project_id}:{run_id}:requests",
                "DUPEFILTER_KEY": f"{ns}:scrapy:{project_id}:{run_id}:dupefilter",
                "REDIS_URL": redis_url,
            }
        )

    return settings
