"""Shared destructive fixture for explicitly named PostgreSQL test databases."""

import os
from urllib.parse import unquote, urlsplit

import asyncpg
import pytest_asyncio

SAFE_DATABASE_MARKERS = ("antcode_test", "antcode_migration_test")


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


@pytest_asyncio.fixture
async def pg_connection():
    database_url = require_test_database_url(os.getenv("TEST_DATABASE_URL", "").strip())
    connection = await asyncpg.connect(database_url)
    actual_database = str(await connection.fetchval("SELECT current_database()"))
    assert_safe_database_name(actual_database)
    await reset_public_schema(connection)
    try:
        yield connection
    finally:
        await reset_public_schema(connection)
        await connection.close()
