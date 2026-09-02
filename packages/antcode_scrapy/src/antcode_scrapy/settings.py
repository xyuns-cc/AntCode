"""按 rule 动态装配 Scrapy settings。

分层：
- 基础设置（并发、超时、日志、pipeline 注册）永远启用
- engine=playwright/render → 装 scrapy-playwright DownloadHandler + asyncio reactor
- engine=curl_cffi → 装 scrapy-impersonate（TLS/JA3 指纹伪装）

安全 spool 模式不向子进程下发 Redis 凭据，因此依赖 Redis 的跨 Worker resume
与动态代理池会显式拒绝；legacy 模式仍支持 scrapy-redis resume 和固定代理。
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
        "DEPTH_LIMIT": int(rule.get("max_depth") or 0),
        # request_delay 从毫秒转秒（对齐 ProjectRule.request_delay 语义）
        "DOWNLOAD_DELAY": _int_rule_value(rule, "request_delay", 1000) / 1000.0,
        "RANDOMIZE_DOWNLOAD_DELAY": True,
        "RETRY_TIMES": _int_rule_value(rule, "retry_count", 3),
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
        "EXTENSIONS": {
            "antcode_scrapy.stats_export.SpiderStatsExporter": 500,
        },
        "DOWNLOADER_MIDDLEWARES": {},
        # 请求指纹 v2.7+ 默认，避免弃用警告
        "REQUEST_FINGERPRINTER_IMPLEMENTATION": "2.7",
    }
    worker_spool_mode, egress_proxy = _configure_worker_egress(settings)
    fixed_proxy = _configure_rule_proxy(settings, rule, worker_spool_mode)
    _configure_resume(settings, rule, worker_spool_mode)

    engine = str(rule.get("engine") or "").lower()
    # R1-P1-13 (审查报告): pagination_config.method=js_click / infinite_scroll
    # 需要 Playwright；老实现 rule.engine 若不是 playwright，spider 侧
    # request meta 挂了 playwright=True 但 handler 未装 → playwright_page
    # 为 None，翻页整段跳过静默单页。这里强制把 engine 提升为 playwright。
    pagination_method = _normalized_pagination_method(rule)
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
                "PLAYWRIGHT_LAUNCH_OPTIONS": _playwright_launch_options(egress_proxy or fixed_proxy),
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

    return settings


def _int_rule_value(rule: dict[str, Any], key: str, default: int) -> int:
    value = rule.get(key)
    return default if value is None else int(value)


def _normalized_pagination_method(rule: dict[str, Any]) -> str:
    method = str((rule.get("pagination_config") or {}).get("method") or "").lower()
    return "infinite_scroll" if method in {"javascript", "ajax"} else method


def _playwright_launch_options(egress_proxy: str) -> dict[str, Any]:
    options: dict[str, Any] = {"headless": True}
    if egress_proxy:
        # SSRF: Chromium 默认对 loopback(localhost/127.0.0.1/::1)绕过代理直连,
        # 会让规则页面直接命中 Worker 本机回环服务。``<-loopback>`` 移除该隐式
        # 绕过,连 loopback 也强制走 Worker 受控出口代理(代理再按 SSRF 策略拒
        # 绝内网目标)。bypass="" 只做兜底,真正生效的是 --proxy-bypass-list。
        options["proxy"] = {"server": egress_proxy, "bypass": ""}
        options["args"] = ["--proxy-bypass-list=<-loopback>"]
    return options


def _configure_worker_egress(settings: dict[str, Any]) -> tuple[bool, str]:
    spool_mode = os.environ.get("ANTCODE_SPIDER_SINK_MODE", "").strip().lower() == "spool"
    proxy_url = os.environ.get("ANTCODE_SPIDER_EGRESS_PROXY", "").strip()
    if not spool_mode:
        return False, proxy_url
    if not proxy_url:
        raise RuntimeError("Rule 缺少 Worker 受控出口代理")
    settings["DOWNLOADER_MIDDLEWARES"] = {
        "antcode_scrapy.safe_egress.SafeEgressProxyMiddleware": 749,
    }
    return True, proxy_url


def _configure_rule_proxy(
    settings: dict[str, Any],
    rule: dict[str, Any],
    worker_spool_mode: bool,
) -> str:
    proxy_cfg = rule.get("proxy_config") or {}
    if not proxy_cfg.get("enabled"):
        return ""
    if worker_spool_mode:
        raise RuntimeError("Rule proxy_config 尚未迁移到 Worker 受控出口，拒绝绕过安全代理")
    from antcode_scrapy.proxy import resolve_fixed_proxy_url

    proxy_url = resolve_fixed_proxy_url(proxy_cfg)
    settings["DOWNLOADER_MIDDLEWARES"] = {
        **settings.get("DOWNLOADER_MIDDLEWARES", {}),
        "antcode_scrapy.proxy.AntCodeProxyMiddleware": 749,
    }
    return proxy_url


def _configure_resume(
    settings: dict[str, Any],
    rule: dict[str, Any],
    worker_spool_mode: bool,
) -> None:
    if not rule.get("resume_enabled"):
        return
    if worker_spool_mode:
        raise RuntimeError("rule.resume_enabled 尚无 Worker 父进程 checkpoint；拒绝向 Rule 子进程下发 Redis 凭据")
    _configure_legacy_redis_resume(settings)


def _configure_legacy_redis_resume(settings: dict[str, Any]) -> None:
    redis_url = os.environ.get("ANTCODE_SPIDER_REDIS_URL", "").strip()
    if not redis_url:
        raise RuntimeError("legacy rule.resume_enabled 需要 ANTCODE_SPIDER_REDIS_URL")
    project_id = os.environ.get("ANTCODE_SPIDER_PROJECT_ID", "") or "default"
    namespace = os.environ.get("ANTCODE_SPIDER_REDIS_NAMESPACE", "") or "antcode"
    run_id = os.environ.get("ANTCODE_SPIDER_RUN_ID", "") or "0"
    settings.update(
        {
            "SCHEDULER": "scrapy_redis.scheduler.Scheduler",
            "SCHEDULER_PERSIST": False,
            "DUPEFILTER_CLASS": "scrapy_redis.dupefilter.RFPDupeFilter",
            "SCHEDULER_QUEUE_KEY": f"{namespace}:scrapy:{project_id}:{run_id}:requests",
            "DUPEFILTER_KEY": f"{namespace}:scrapy:{project_id}:{run_id}:dupefilter",
            "REDIS_URL": redis_url,
        }
    )
