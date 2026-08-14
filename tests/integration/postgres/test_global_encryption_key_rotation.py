from __future__ import annotations

import json

import pytest
from antcode_core.application.services.security.postgres_encryption_key_rotation import (
    rotate_postgres_ciphertexts,
    verify_postgres_ciphertexts_primary_only,
)
from antcode_core.common.config import settings
from antcode_core.common.security.api_key import hash_api_key
from antcode_core.common.security.secret_box import SecretBox
from cryptography.fernet import Fernet

EXPECTED_REWRITTEN_ROWS = 7


class _ConnectionAdapter:
    def __init__(self, connection) -> None:
        self._connection = connection

    async def execute_query_dict(self, sql: str, params: list | None = None) -> list[dict]:
        return [dict(row) for row in await self._connection.fetch(sql, *(params or []))]

    async def execute_query(self, sql: str, params: list | None = None) -> tuple[int, list]:
        status = await self._connection.execute(sql, *(params or []))
        affected = int(status.rsplit(" ", 1)[-1]) if status.startswith("UPDATE ") else 0
        return affected, []


def _configure_box(monkeypatch) -> tuple[SecretBox, Fernet]:
    primary = Fernet.generate_key()
    legacy = Fernet.generate_key()
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", primary.decode())
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS_LEGACY", legacy.decode())
    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_ALLOW_LEGACY_SHA256", False)
    return SecretBox(), Fernet(legacy)


def _json_envelope(legacy: Fernet, value: dict) -> str:
    plaintext = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    token = legacy.encrypt(plaintext.encode()).decode()
    return json.dumps({"__antcode_encrypted_v1__": token})


async def _create_tables(connection) -> None:
    await connection.execute(
        """
        CREATE TABLE git_credentials (id BIGINT PRIMARY KEY, secret_encrypted TEXT NOT NULL);
        CREATE TABLE system_configs (id BIGINT PRIMARY KEY, config_value TEXT NOT NULL);
        CREATE TABLE project_files (id BIGINT PRIMARY KEY, runtime_config JSONB, environment_vars JSONB);
        CREATE TABLE project_codes (id BIGINT PRIMARY KEY, runtime_config JSONB, environment_vars JSONB);
        CREATE TABLE project_rules (
          id BIGINT PRIMARY KEY, headers JSONB, cookies JSONB, proxy_config JSONB, task_config JSONB
        );
        CREATE TABLE scheduled_tasks (id BIGINT PRIMARY KEY, execution_params JSONB, environment_vars JSONB);
        CREATE TABLE workers (
          id BIGINT PRIMARY KEY, public_id TEXT NOT NULL, secret_key_hash TEXT, secret_key_encrypted TEXT,
          redis_username TEXT, redis_password_encrypted TEXT
        );
        """
    )


async def _seed_tables(connection, legacy: Fernet) -> None:
    json_value = _json_envelope(legacy, {"secret": "value"})
    await connection.execute("INSERT INTO git_credentials VALUES ($1, $2)", 1, legacy.encrypt(b"git-secret").decode())
    await connection.execute(
        "INSERT INTO system_configs VALUES ($1, $2)", 1, "enc:v1:" + legacy.encrypt(b"config").decode()
    )
    for table in ("project_files", "project_codes", "scheduled_tasks"):
        await connection.execute(f"INSERT INTO {table} VALUES ($1, $2::jsonb, $3::jsonb)", 1, json_value, json_value)
    await connection.execute(
        "INSERT INTO project_rules VALUES ($1, $2::jsonb, $3::jsonb, $4::jsonb, $5::jsonb)",
        1,
        json_value,
        json_value,
        json_value,
        json_value,
    )
    worker_secret = "integration-worker-secret"
    await connection.execute(
        "INSERT INTO workers VALUES ($1, $2, $3, $4, $5, $6)",
        1,
        "worker-1",
        hash_api_key(worker_secret),
        legacy.encrypt(worker_secret.encode()).decode(),
        "worker_1",
        legacy.encrypt(b"redis-password").decode(),
    )


@pytest.mark.asyncio
async def test_real_postgres_global_rotation_is_atomic_and_primary_only(pg_connection, monkeypatch) -> None:
    box, legacy = _configure_box(monkeypatch)
    await _create_tables(pg_connection)
    await _seed_tables(pg_connection, legacy)
    adapter = _ConnectionAdapter(pg_connection)

    async with pg_connection.transaction():
        result = await rotate_postgres_ciphertexts(adapter, apply=True, box=box)

    assert result.rows_rewritten == EXPECTED_REWRITTEN_ROWS
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS_LEGACY", "")
    async with pg_connection.transaction():
        verified = await verify_postgres_ciphertexts_primary_only(adapter, box=box)
    assert verified.ciphertexts_scanned == result.ciphertexts_scanned


@pytest.mark.asyncio
async def test_real_postgres_global_rotation_rolls_back_all_tables(pg_connection, monkeypatch) -> None:
    box, legacy = _configure_box(monkeypatch)
    await _create_tables(pg_connection)
    await _seed_tables(pg_connection, legacy)
    before = await pg_connection.fetchval("SELECT secret_encrypted FROM git_credentials WHERE id=1")
    await pg_connection.execute("UPDATE workers SET secret_key_hash=$1", hash_api_key("different"))
    adapter = _ConnectionAdapter(pg_connection)

    with pytest.raises(RuntimeError, match="完整性校验失败"):
        async with pg_connection.transaction():
            await rotate_postgres_ciphertexts(adapter, apply=True, box=box)

    assert await pg_connection.fetchval("SELECT secret_encrypted FROM git_credentials WHERE id=1") == before


@pytest.mark.asyncio
async def test_real_postgres_rotation_timeout_configuration_is_valid(pg_connection, monkeypatch) -> None:
    box, _legacy = _configure_box(monkeypatch)
    await _create_tables(pg_connection)
    adapter = _ConnectionAdapter(pg_connection)

    async with pg_connection.transaction():
        await rotate_postgres_ciphertexts(adapter, apply=False, box=box)
        assert await pg_connection.fetchval("SHOW lock_timeout") == "30s"
        assert await pg_connection.fetchval("SHOW statement_timeout") == "30min"
