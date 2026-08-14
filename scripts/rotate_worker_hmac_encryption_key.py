"""Safely rotate persisted Worker credentials to the current ENCRYPTION_KEY."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "antcode_core" / "src"))


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="在单个 PostgreSQL 事务中写入轮换后的密文")
    mode.add_argument("--verify-primary-only", action="store_true", help="确认所有 Worker 凭据仅靠主密钥即可解密")
    parser.add_argument(
        "--confirm-writers-stopped",
        action="store_true",
        help="确认 Web API、Master、Gateway 等凭据 writer 均已停止",
    )
    return parser.parse_args(argv)


def _validate_arguments(args: argparse.Namespace) -> None:
    if args.apply and not args.confirm_writers_stopped:
        raise RuntimeError("--apply 必须同时提供 --confirm-writers-stopped")
    if args.confirm_writers_stopped and not args.apply:
        raise RuntimeError("--confirm-writers-stopped 仅可与 --apply 一起使用")


async def _run(args: argparse.Namespace):
    from antcode_core.application.services.workers.worker_hmac_key_rotation import (
        rotate_worker_credentials,
        verify_worker_credentials_primary_only,
    )
    from tortoise.transactions import in_transaction

    if args.verify_primary_only:
        async with in_transaction("default") as connection:
            count = await verify_worker_credentials_primary_only(connection)
        print(f"Worker 主密钥独立验证通过: workers={count}")
        return count
    async with in_transaction("default") as connection:
        result = await rotate_worker_credentials(connection, apply=args.apply)
    label = "已提交" if args.apply else "dry-run"
    print(
        f"Worker 凭据轮换 {label}: workers={result.workers_scanned}, "
        f"hmac={result.hmac_secrets_scanned}, redis_acl={result.redis_passwords_scanned}, "
        f"needs_rotation={result.ciphertexts_requiring_rotation}, rewritten={result.rows_rewritten}"
    )
    return result


async def async_main(argv: list[str] | None = None):
    args = _arguments(argv)
    _validate_arguments(args)
    load_dotenv(ROOT / ".env")
    from antcode_core.infrastructure.db.tortoise import close_db, init_db

    await init_db(service="migration")
    failure: BaseException | None = None
    try:
        return await _run(args)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            await close_db()
        except BaseException as close_failure:
            if failure is not None:
                raise BaseExceptionGroup("Worker 凭据轮换与数据库关闭均失败", [failure, close_failure]) from failure
            raise


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
