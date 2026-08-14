"""One-time transactional rewrite of historical plaintext sensitive fields."""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv
from tortoise import Tortoise
from tortoise.transactions import in_transaction

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=ROOT / ".env")

from antcode_core.domain.models import ProjectCode, ProjectFile, ProjectRule, SystemConfig, Task  # noqa: E402
from antcode_core.infrastructure.db.tortoise import close_db, init_db  # noqa: E402

MIGRATION_NAME = "20260730_encrypt_sensitive_fields_v1"
REWRITE_BATCH_SIZE = 200
_CREATE_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS antcode_data_migrations (
    name VARCHAR(128) PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _model_specs():
    return (
        ("system_configs", SystemConfig, ["config_value"]),
        ("project_files", ProjectFile, ["runtime_config", "environment_vars"]),
        ("project_codes", ProjectCode, ["runtime_config", "environment_vars"]),
        ("project_rules", ProjectRule, ["headers", "cookies", "proxy_config", "task_config"]),
        ("tasks", Task, ["execution_params", "environment_vars"]),
    )


async def _rewrite(model, fields: list[str], connection) -> int:
    """Rewrite rows in bounded keyset pages while holding row locks."""
    rewritten = 0
    last_id = 0
    while True:
        rows = await (
            model.filter(id__gt=last_id)
            .using_db(connection)
            .select_for_update()
            .order_by("id")
            .limit(REWRITE_BATCH_SIZE)
        )
        if not rows:
            return rewritten
        for row in rows:
            await row.save(using_db=connection, update_fields=fields)
        rewritten += len(rows)
        last_id = rows[-1].id


async def _already_applied(connection) -> bool:
    rows = await connection.execute_query_dict(
        "SELECT 1 AS ok FROM antcode_data_migrations WHERE name = $1",
        [MIGRATION_NAME],
    )
    return bool(rows)


async def migrate_sensitive_data() -> dict[str, int]:
    """Apply the rewrite once; any failure rolls back data and the ledger mark."""
    connection = Tortoise.get_connection("default")
    await connection.execute_query(_CREATE_LEDGER_SQL)
    async with in_transaction("default") as transaction:
        await transaction.execute_query(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            [MIGRATION_NAME],
        )
        if await _already_applied(transaction):
            return {}
        counts = {name: await _rewrite(model, fields, transaction) for name, model, fields in _model_specs()}
        await transaction.execute_query(
            "INSERT INTO antcode_data_migrations (name) VALUES ($1)",
            [MIGRATION_NAME],
        )
        return counts


async def main() -> dict[str, int]:
    await init_db(service="migration")
    failure: BaseException | None = None
    try:
        counts = await migrate_sensitive_data()
        print(counts)
        return counts
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            await close_db()
        except BaseException as close_failure:
            if failure is not None:
                raise BaseExceptionGroup(
                    "敏感字段迁移与数据库关闭均失败",
                    [failure, close_failure],
                ) from failure
            raise


if __name__ == "__main__":
    asyncio.run(main())
