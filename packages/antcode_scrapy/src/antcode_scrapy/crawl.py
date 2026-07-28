"""AntCode 规则爬虫 CLI（Scrapy 执行引擎）。

由 ``RulePlugin.build_plan`` 生成的执行命令直接调用：

    python -m antcode_scrapy.crawl --rule-file /path/to/rule.json

也支持 ``--rule-json`` 内联字符串或 env ``ANTCODE_RULE_JSON``（与旧
run_rule.py 参数完全兼容，方便切换）。

**约束**：
- 参数缺失/规则无效必须 exit != 0，禁止静默失败
- run_id / project_id 通过 env 拿：``ANTCODE_SPIDER_RUN_ID`` 等
- 退出码语义：仅当至少抓到一条且全部持久化成功时返回 0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CrawlOutcome:
    items: int
    errors: int
    written: int
    xadd_failed: int
    final_flush_failed: int
    final_flush_remaining: int


def _result_exit_code(outcome: CrawlOutcome) -> int:
    if outcome.final_flush_failed > 0 or outcome.final_flush_remaining > 0:
        return 1
    if outcome.xadd_failed > 0:
        return 1
    if outcome.items <= 0:
        return 1
    return 0 if outcome.written >= outcome.items else 1


def _load_rule(args: argparse.Namespace) -> dict[str, Any]:
    if args.rule_file:
        raw = Path(args.rule_file).read_text(encoding="utf-8")
    elif args.rule_json:
        raw = args.rule_json
    else:
        env_val = os.environ.get("ANTCODE_RULE_JSON") or ""
        if not env_val:
            raise SystemExit("缺少规则输入：--rule-file / --rule-json / env ANTCODE_RULE_JSON 三选一")
        raw = env_val
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"规则 JSON 解析失败: {exc}") from exc


def _run(rule: dict[str, Any]) -> int:
    """启动 Scrapy CrawlerProcess 跑一次爬取，返回退出码。"""
    # 延迟 import Scrapy：让 --help 之类的入口不用装 Scrapy 也能跑
    from scrapy.crawler import CrawlerProcess

    from antcode_scrapy.settings import build_settings
    from antcode_scrapy.spiders.rule_spider import UniversalRuleSpider

    run_id = os.environ.get("ANTCODE_SPIDER_RUN_ID", "") or ""
    project_id = os.environ.get("ANTCODE_SPIDER_PROJECT_ID", "") or ""

    settings = build_settings(rule)
    process = CrawlerProcess(settings=settings, install_root_handler=True)

    # 用 crawl_defer 拿到 Crawler 对象，close 时读 stats
    crawler = process.create_crawler(UniversalRuleSpider)
    process.crawl(crawler, rule=rule, run_id=run_id, project_id=project_id)
    process.start()  # 阻塞直到所有 spider 结束

    stats = crawler.stats
    if stats is None:
        raise RuntimeError("Scrapy crawler 未初始化统计收集器")
    items = int(stats.get_value("item_scraped_count", 0) or 0)
    errors = int(stats.get_value("log_count/ERROR", 0) or 0)
    finish_reason = stats.get_value("finish_reason", "unknown")
    # R1-P1-9 (审查报告): 单看 log_count/ERROR 会漏掉走 loguru 打的错误
    # （loguru 与 stdlib logging 隔离）。补看 Redis pipeline 自己写的
    # ``antcode/redis_xadd_failed`` 与 ``antcode/redis_items_written``，
    # 覆盖 "Redis 宕机时每条 item 被吞、items>0 但 written=0" 的假成功场景。
    xadd_failed = int(stats.get_value("antcode/redis_xadd_failed", 0) or 0)
    written = int(stats.get_value("antcode/redis_items_written", 0) or 0)
    # P1-27: pipeline close_spider 里 sink.close 报告的最终 flush 情况——
    # 老实说这两个字段是新加的，None 表示没触发（direct 模式 close 返回 None）。
    final_flush_failed = int(stats.get_value("antcode/final_flush_failed", 0) or 0)
    final_flush_remaining = int(stats.get_value("antcode/final_flush_remaining", 0) or 0)

    print(
        f"[antcode-scrapy] finish_reason={finish_reason} "
        f"items={items} written={written} errors={errors} "
        f"xadd_failed={xadd_failed} final_flush_failed={final_flush_failed} "
        f"final_flush_remaining={final_flush_remaining}"
    )

    return _result_exit_code(
        CrawlOutcome(
            items=items,
            errors=errors,
            written=written,
            xadd_failed=xadd_failed,
            final_flush_failed=final_flush_failed,
            final_flush_remaining=final_flush_remaining,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="AntCode Scrapy 规则爬虫 CLI")
    parser.add_argument("--rule-file", help="规则 JSON 文件路径")
    parser.add_argument("--rule-json", help="规则 JSON 内联字符串")
    args = parser.parse_args()

    rule = _load_rule(args)
    return _run(rule)


if __name__ == "__main__":
    sys.exit(main())
