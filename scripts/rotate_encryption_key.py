"""Rotate every persisted ENCRYPTION_KEY ciphertext without exposing secrets."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "antcode_core" / "src"))


@dataclass(frozen=True)
class RotationCommandResult:
    mode: str
    ciphertexts_scanned: int
    ciphertexts_requiring_rotation: int
    rows_rewritten: int


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="在单个 PostgreSQL 事务中提交全域密文轮换")
    mode.add_argument("--verify-primary-only", action="store_true", help="确认全部持久密文只依赖当前主密钥")
    parser.add_argument(
        "--confirm-writers-stopped",
        action="store_true",
        help="确认 Web API、Master、Gateway、Worker 和维护任务的相关 writer 均已停止",
    )
    return parser.parse_args(argv)


def _validate_arguments(args: argparse.Namespace) -> None:
    if (args.apply or args.verify_primary_only) and not args.confirm_writers_stopped:
        raise RuntimeError("--apply/--verify-primary-only 必须同时提供 --confirm-writers-stopped")


async def _run(args: argparse.Namespace, redis):
    from antcode_core.application.services.security.postgres_encryption_key_rotation import (
        rotate_postgres_ciphertexts,
        verify_postgres_ciphertexts_primary_only,
    )
    from antcode_core.application.services.security.redispatch_rotation_guard import (
        inspect_redispatch_drain,
        require_redispatch_drained,
    )
    from antcode_core.common.config import settings
    from tortoise.transactions import in_transaction

    namespace = str(settings.REDIS_NAMESPACE)
    require_redispatch_drained(await inspect_redispatch_drain(redis, namespace))
    async with in_transaction("default") as connection:
        if args.verify_primary_only:
            result = await verify_postgres_ciphertexts_primary_only(connection)
        else:
            result = await rotate_postgres_ciphertexts(connection, apply=args.apply)
        require_redispatch_drained(await inspect_redispatch_drain(redis, namespace))
    return result


def _summarize(args: argparse.Namespace, result) -> RotationCommandResult:
    if args.verify_primary_only:
        mode = "primary-only"
    elif args.apply:
        mode = "apply"
    else:
        mode = "dry-run"
    summary = RotationCommandResult(
        mode=mode,
        ciphertexts_scanned=result.ciphertexts_scanned,
        ciphertexts_requiring_rotation=result.ciphertexts_requiring_rotation,
        rows_rewritten=result.rows_rewritten,
    )
    print(
        f"全域主密钥轮换 {summary.mode}: ciphertexts={summary.ciphertexts_scanned}, "
        f"needs_rotation={summary.ciphertexts_requiring_rotation}, rewritten={summary.rows_rewritten}"
    )
    for table in result.tables:
        print(
            f"table={table.table} rows={table.rows_scanned} ciphertexts={table.ciphertexts_scanned} "
            f"needs_rotation={table.ciphertexts_requiring_rotation} rewritten={table.rows_rewritten}"
        )
    print(
        f"table=workers rows={result.workers.workers_scanned} "
        f"ciphertexts={result.workers.hmac_secrets_scanned + result.workers.redis_passwords_scanned} "
        f"needs_rotation={result.workers.ciphertexts_requiring_rotation} rewritten={result.workers.rows_rewritten}"
    )
    return summary


async def async_main(argv: list[str] | None = None) -> RotationCommandResult:
    args = _arguments(argv)
    _validate_arguments(args)
    load_dotenv(ROOT / ".env")
    from antcode_core.infrastructure.db.tortoise import close_db, init_db
    from antcode_core.infrastructure.redis.factory import create_async_redis_client

    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        raise RuntimeError("REDIS_URL 未配置")
    await init_db(service="migration")
    redis = create_async_redis_client(redis_url, decode_responses=False)
    failure: BaseException | None = None
    try:
        if not await cast(Awaitable[bool], redis.ping()):
            raise RuntimeError("Redis PING 未返回成功")
        return _summarize(args, await _run(args, redis))
    except BaseException as exc:
        failure = exc
        raise
    finally:
        await _close_resources(redis, close_db, failure)


async def _close_resources(redis, close_db, failure: BaseException | None) -> None:
    close_failures: list[BaseException] = []
    for operation in (redis.aclose, close_db):
        try:
            await operation()
        except BaseException as exc:
            close_failures.append(exc)
    if not close_failures:
        return
    if failure is not None:
        raise BaseExceptionGroup("全域密钥轮换与资源关闭均失败", [failure, *close_failures]) from failure
    if len(close_failures) == 1:
        raise close_failures[0]
    raise BaseExceptionGroup("全域密钥轮换资源关闭失败", close_failures)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
