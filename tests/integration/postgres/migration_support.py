"""Safety guards and helpers for real PostgreSQL migration tests."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "migrations/models"
MIGRATION_PATHS = tuple(sorted(MIGRATIONS_DIR.glob("*.sql")))
MIGRATION_PATH_BY_NAME = {path.name: path for path in MIGRATION_PATHS}
SAFE_DATABASE_MARKERS = ("antcode_test", "antcode_migration_test")


@dataclass(frozen=True, kw_only=True)
class SchemaExpectation:
    table: str
    columns: tuple[str, ...]
    absent_columns: tuple[str, ...] = ()
    indexes: tuple[str, ...] = ()
    column_comment: tuple[str, str] | None = None


@dataclass(frozen=True, kw_only=True)
class FailureExpectation:
    setup_sql: str
    marker_query: str
    marker_value: object
    present_columns: tuple[str, ...] = ()
    absent_columns: tuple[str, ...] = ()
    absent_indexes: tuple[str, ...] = ()
    target_exists: bool = True


@dataclass(frozen=True, kw_only=True)
class MigrationCase:
    name: str
    setup_sql: str
    seed_after_first_sql: str
    marker_query: str
    marker_value: object
    schema: SchemaExpectation
    failure: FailureExpectation


def require_test_database_url(value: str | None = None) -> str:
    """Return an explicitly configured URL only when its database is test-only."""
    url = value if value is not None else os.getenv("TEST_DATABASE_URL")
    url = (url or "").strip()
    if not url:
        raise RuntimeError("必须显式设置 TEST_DATABASE_URL")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"postgres", "postgresql"}:
        raise ValueError("TEST_DATABASE_URL 必须使用 PostgreSQL")
    database = unquote(parsed.path.lstrip("/")).lower()
    if not database or not any(marker in database for marker in SAFE_DATABASE_MARKERS):
        markers = " 或 ".join(SAFE_DATABASE_MARKERS)
        raise ValueError(f"TEST_DATABASE_URL 数据库名必须包含 {markers}")
    return url


def assert_safe_database_name(database: str) -> None:
    normalized = (database or "").lower()
    if not any(marker in normalized for marker in SAFE_DATABASE_MARKERS):
        raise RuntimeError(f"拒绝操作非测试数据库: {database!r}")


async def reset_public_schema(connection) -> None:
    database = await connection.fetchval("SELECT current_database()")
    assert_safe_database_name(str(database))
    await connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
    await connection.execute("CREATE SCHEMA public")


async def apply_migrations(connection) -> None:
    for path in MIGRATION_PATHS:
        await connection.execute(path.read_text(encoding="utf-8"))


async def apply_migration(connection, name: str) -> None:
    path = MIGRATION_PATH_BY_NAME[name]
    await connection.execute(path.read_text(encoding="utf-8"))


async def rollback_failed_migration(connection) -> None:
    if connection.is_in_transaction():
        await connection.execute("ROLLBACK")


async def column_names(connection, table: str) -> set[str]:
    rows = await connection.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = $1",
        table,
    )
    return {str(row["column_name"]) for row in rows}


async def index_names(connection, table: str) -> set[str]:
    rows = await connection.fetch(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = $1",
        table,
    )
    return {str(row["indexname"]) for row in rows}


async def relation_exists(connection, table: str) -> bool:
    return await connection.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{table}")


async def column_comment(connection, table: str, column: str) -> str | None:
    return await connection.fetchval(
        """
        SELECT pg_catalog.col_description(columns.table_name::regclass, columns.ordinal_position)
        FROM information_schema.columns
        WHERE columns.table_schema = 'public'
          AND columns.table_name = $1
          AND columns.column_name = $2
        """,
        table,
        column,
    )


class AsyncpgTortoiseConnection:
    """Small adapter for exercising the real install-key migration function."""

    def __init__(self, connection) -> None:
        self._connection = connection

    async def execute_query_dict(self, sql: str, params: list) -> list[dict]:
        rows = await self._connection.fetch(sql, *params)
        return [dict(row) for row in rows]

    async def execute_query(self, sql: str, params: list) -> tuple[int, list]:
        status = await self._connection.execute(sql, *params)
        affected = int(status.rsplit(" ", 1)[-1])
        return affected, []


class MetadataRedis:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    async def get(self, key: str) -> str | None:
        return self.values.get(key)
