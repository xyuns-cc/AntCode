"""Offline Crawl Redis fresh-deploy preflight.

Run as ``python -m scripts.migrate_crawl_redis``. Read-only，没有任何写开关：它
证明目标 Redis 是干净的——没有旧 key、没有当前 Crawl 数据、执行队列已排空、没有
不受支持的 envelope。任一条不成立即以非零码阻断控制面启动。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Awaitable, Sequence
from typing import cast

from antcode_core.infrastructure.redis.factory import create_async_redis_client

from scripts.crawl_redis_upgrade_contract import UpgradeBlocked, UpgradeReport, UpgradeRequest
from scripts.crawl_redis_upgrade_scan import build_report


async def execute(client, request: UpgradeRequest) -> UpgradeReport:
    report = await build_report(client, request)
    if report.blockers:
        raise UpgradeBlocked(report)
    return report


async def run(url: str, request: UpgradeRequest) -> UpgradeReport:
    request.validate()
    client = create_async_redis_client(url, decode_responses=False)
    failure: BaseException | None = None
    try:
        if not await cast(Awaitable[bool], client.ping()):
            raise RuntimeError("Redis PING 未返回成功")
        return await execute(client, request)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            await client.aclose()
        except BaseException as close_exc:
            if failure is not None:
                raise BaseExceptionGroup("Crawl Redis 预检与客户端关闭均失败", [failure, close_exc]) from failure
            raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crawl Redis 全新部署离线预检（只读）")
    parser.add_argument("--url", help="目标 Redis URL；默认读取 REDIS_URL")
    parser.add_argument("--namespace", help="目标 namespace；默认读取 REDIS_NAMESPACE")
    return parser


def _required_setting(value: str | None, environment_name: str) -> str:
    candidate = os.environ.get(environment_name, "") if value is None else value
    resolved = candidate.strip()
    if not resolved:
        raise ValueError(f"必须通过参数或 {environment_name} 提供配置")
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        url = _required_setting(args.url, "REDIS_URL")
        namespace = _required_setting(args.namespace, "REDIS_NAMESPACE")
    except ValueError as exc:
        parser.error(str(exc))
    try:
        report = asyncio.run(run(url, UpgradeRequest(namespace)))
    except UpgradeBlocked as exc:
        print(json.dumps(exc.report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["execute", "main", "run"]
