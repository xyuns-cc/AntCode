"""AntCode 规则爬虫 CLI（Scrapy 执行引擎）。

由 ``RulePlugin.build_plan`` 生成的执行命令直接调用：

    python -m antcode_scrapy.crawl --rule-file /path/to/rule.json

也支持 ``--rule-json`` 内联字符串或 env ``ANTCODE_RULE_JSON``（与旧
run_rule.py 参数完全兼容，方便切换）。

**约束（对齐旧 run_rule.py）**：
- 参数缺失/规则无效必须 exit != 0，禁止静默失败
- run_id / project_id 通过 env 拿：``ANTCODE_SPIDER_RUN_ID`` 等
- 退出码语义：抓到 item → 0；无 item 但无严重错误 → 0；有 ERROR 且无 item → 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _load_rule(args: argparse.Namespace) -> dict[str, Any]:
    if args.rule_file:
        raw = Path(args.rule_file).read_text(encoding="utf-8")
    elif args.rule_json:
        raw = args.rule_json
    else:
        env_val = os.environ.get("ANTCODE_RULE_JSON") or ""
        if not env_val:
            raise SystemExit(
                "缺少规则输入：--rule-file / --rule-json / env ANTCODE_RULE_JSON 三选一"
            )
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
    final_flush_failed = int(
        stats.get_value("antcode/final_flush_failed", 0) or 0
    )
    final_flush_remaining = int(
        stats.get_value("antcode/final_flush_remaining", 0) or 0
    )

    print(
        f"[antcode-scrapy] finish_reason={finish_reason} "
        f"items={items} written={written} errors={errors} "
        f"xadd_failed={xadd_failed} final_flush_failed={final_flush_failed} "
        f"final_flush_remaining={final_flush_remaining}"
    )

    # P1-27: close 阶段 flush 失败或 buffer 剩余数据 → 非零退出
    if final_flush_failed > 0 or final_flush_remaining > 0:
        return 1
    # R1-P1-9: xadd 有失败 → 非零退出
    if xadd_failed > 0:
        return 1
    # items > 0 但一条都没写进 Redis → 假成功场景，非零退出
    if items > 0 and written == 0:
        return 1
    # P1-27: items > written（有条目没被 sink ack）也算失败，
    # 覆盖 gateway 模式 buffer 里还有条目没送出去的情况
    if items > 0 and written < items:
        return 1
    # 与旧 run_rule.py 语义：有 item 即成功；无 item 且有 ERROR → 失败
    if items > 0:
        return 0
    if errors > 0:
        return 1
    # 无 item 也无 error（页面结构变了、CSS 不命中等）不判失败
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AntCode Scrapy 规则爬虫 CLI")
    parser.add_argument("--rule-file", help="规则 JSON 文件路径")
    parser.add_argument("--rule-json", help="规则 JSON 内联字符串")
    args = parser.parse_args()

    rule = _load_rule(args)
    return _run(rule)


if __name__ == "__main__":
    sys.exit(main())
