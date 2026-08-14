"""Shared destructive fixture for explicitly named migration-test databases."""

import os

import asyncpg
import pytest_asyncio

from .migration_support import assert_safe_database_name, require_test_database_url, reset_public_schema


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
