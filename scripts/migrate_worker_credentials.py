"""把历史 Worker 明文凭据迁移为 API Key 哈希和 HMAC secret 密文。"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "antcode_core" / "src"))


async def _plaintext_columns(connection) -> set[str]:
    rows = await connection.execute_query_dict(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='workers' "
        "AND column_name IN ('api_key', 'secret_key', 'api_key_previous')"
    )
    return {str(row["column_name"]) for row in rows}


async def _migrate_rows(connection) -> int:
    from antcode_core.common.security.api_key import hash_api_key
    from antcode_core.common.security.secret_box import secret_box

    rows = await connection.execute_query_dict(
        'SELECT "id", "api_key", "secret_key", "api_key_previous" FROM "workers"'
    )
    for row in rows:
        api_key = row.get("api_key")
        secret_key = row.get("secret_key")
        previous = row.get("api_key_previous")
        affected, _ = await connection.execute_query(
            'UPDATE "workers" SET "api_key_hash"=$1, "secret_key_hash"=$2, '
            '"secret_key_encrypted"=$3, "api_key_previous_hash"=$4 WHERE "id"=$5',
            [
                hash_api_key(api_key) if api_key else None,
                hash_api_key(secret_key) if secret_key else None,
                secret_box.encrypt(secret_key) if secret_key else None,
                hash_api_key(previous) if previous else None,
                row["id"],
            ],
        )
        if int(affected) != 1:
            raise RuntimeError(f"Worker 凭据迁移更新行数异常: id={row['id']}, affected={affected}")
    return len(rows)


async def main() -> None:
    load_dotenv(ROOT / ".env")
    if not os.getenv("ENCRYPTION_KEY", "").strip():
        raise RuntimeError("ENCRYPTION_KEY 未配置")

    from antcode_core.infrastructure.db.tortoise import close_db, init_db
    from tortoise.transactions import in_transaction

    await init_db(service="web_api")
    failure: BaseException | None = None
    try:
        async with in_transaction() as connection:
            columns = await _plaintext_columns(connection)
            if not columns:
                print("Worker 明文凭据列已移除，无需迁移")
            elif columns != {"api_key", "secret_key", "api_key_previous"}:
                raise RuntimeError(f"Worker 明文凭据列不完整: {sorted(columns)}")
            else:
                migrated = await _migrate_rows(connection)
                await connection.execute_query(
                    'ALTER TABLE "workers" DROP COLUMN "api_key", DROP COLUMN "secret_key", '
                    'DROP COLUMN "api_key_previous"'
                )
                print(f"已安全迁移 {migrated} 个 Worker 的凭据")
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            await close_db()
        except BaseException as close_failure:
            if failure is not None:
                raise BaseExceptionGroup("Worker 凭据迁移与数据库关闭均失败", [failure, close_failure]) from failure
            raise


if __name__ == "__main__":
    asyncio.run(main())
