"""Offline Crawl Redis namespace/envelope upgrade preflight and migration.

Run as ``python -m scripts.migrate_crawl_redis``. The command is read-only by
default. Existing upgrades require an explicit
stop-writers confirmation and explicit paused project/batch declarations.
Only paused progress/checkpoint Hashes and exact-dedup Sets are migrated.
Execution queues are never rewritten and must be completely drained.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Awaitable, Sequence
from dataclasses import replace
from typing import cast

from antcode_core.infrastructure.redis.factory import create_async_redis_client

from scripts.crawl_redis_upgrade_contract import (
    UpgradeBlocked,
    UpgradeMode,
    UpgradeReport,
    UpgradeRequest,
)
from scripts.crawl_redis_upgrade_migration import migrate_state_keys
from scripts.crawl_redis_upgrade_scan import build_report

PAUSED_BATCH_PARTS = 2


def _paused_batch(value: str) -> tuple[str, str]:
    parts = value.split(":")
    if len(parts) != PAUSED_BATCH_PARTS or not all(parts):
        raise argparse.ArgumentTypeError("paused batch 必须使用 PROJECT_ID:BATCH_ID")
    return parts[0], parts[1]


async def execute(client, request: UpgradeRequest) -> UpgradeReport:
    report = await build_report(client, request)
    if report.blockers:
        raise UpgradeBlocked(report)
    if not request.apply:
        return report
    migrated = await migrate_state_keys(client, report.state_keys)
    return replace(report, migrated_sources=migrated)


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
                raise BaseExceptionGroup("Crawl Redis upgrade 与客户端关闭均失败", [failure, close_exc]) from failure
            raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crawl Redis 离线升级预检/迁移")
    parser.add_argument("--url", help="目标 Redis URL；默认读取 REDIS_URL")
    parser.add_argument("--namespace", help="目标 namespace；默认读取 REDIS_NAMESPACE")
    parser.add_argument("--mode", required=True, choices=[mode.value for mode in UpgradeMode])
    parser.add_argument("--confirm-writers-stopped", action="store_true")
    parser.add_argument("--paused-project", action="append", default=[])
    parser.add_argument("--paused-batch", action="append", default=[], type=_paused_batch)
    parser.add_argument("--apply", action="store_true", help="写入可迁移状态；默认仅预检")
    return parser


def _required_setting(value: str | None, environment_name: str) -> str:
    candidate = os.environ.get(environment_name, "") if value is None else value
    resolved = candidate.strip()
    if not resolved:
        raise ValueError(f"必须通过参数或 {environment_name} 提供配置")
    return resolved


def _request(args: argparse.Namespace, namespace: str) -> UpgradeRequest:
    return UpgradeRequest(
        mode=UpgradeMode(args.mode),
        namespace=namespace,
        apply=bool(args.apply),
        writers_stopped=bool(args.confirm_writers_stopped),
        paused_batches=frozenset(args.paused_batch),
        paused_projects=frozenset(args.paused_project),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        url = _required_setting(args.url, "REDIS_URL")
        namespace = _required_setting(args.namespace, "REDIS_NAMESPACE")
    except ValueError as exc:
        parser.error(str(exc))
    try:
        report = asyncio.run(run(url, _request(args, namespace)))
    except UpgradeBlocked as exc:
        print(json.dumps(exc.report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["execute", "main", "run"]
