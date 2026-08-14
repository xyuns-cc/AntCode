from __future__ import annotations

import pytest
from antcode_core.application.services.workers.worker_hmac_key_rotation import (
    rotate_worker_credentials,
    verify_worker_credentials_primary_only,
)
from antcode_core.common.config import settings
from antcode_core.common.security.api_key import hash_api_key
from antcode_core.common.security.secret_box import secret_box
from cryptography.fernet import Fernet

from scripts.migrate_worker_credentials import migrate_worker_credentials


class _ConnectionAdapter:
    def __init__(self, connection) -> None:
        self._connection = connection

    async def execute_query_dict(self, sql: str, params: list | None = None) -> list[dict]:
        rows = await self._connection.fetch(sql, *(params or []))
        return [dict(row) for row in rows]

    async def execute_query(self, sql: str, params: list | None = None) -> tuple[int, list]:
        status = await self._connection.execute(sql, *(params or []))
        affected = int(status.rsplit(" ", 1)[-1]) if status.startswith("UPDATE ") else 0
        return affected, []


def _configure_encryption(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "")
    secret_box._cached = None
    secret_box._cache_key = None


@pytest.mark.asyncio
async def test_worker_credential_upgrade_is_transactional_and_idempotent(pg_connection, monkeypatch) -> None:
    _configure_encryption(monkeypatch)
    await pg_connection.execute(
        """
        CREATE TABLE public.workers (
            id BIGINT PRIMARY KEY,
            api_key VARCHAR(64), secret_key VARCHAR(128), api_key_previous VARCHAR(64),
            api_key_hash VARCHAR(128), secret_key_hash VARCHAR(128),
            secret_key_encrypted TEXT, api_key_previous_hash VARCHAR(128)
        );
        INSERT INTO public.workers (id, api_key, secret_key, api_key_previous)
        VALUES (1, 'legacy-api', 'legacy-secret', 'legacy-previous');
        """
    )
    adapter = _ConnectionAdapter(pg_connection)

    async with pg_connection.transaction():
        assert await migrate_worker_credentials(adapter) == 1
    async with pg_connection.transaction():
        assert await migrate_worker_credentials(adapter) == 0

    columns = {
        str(row["column_name"])
        for row in await pg_connection.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'workers'"
        )
    }
    row = await pg_connection.fetchrow(
        "SELECT api_key_hash, secret_key_hash, secret_key_encrypted, api_key_previous_hash "
        "FROM public.workers WHERE id = 1"
    )
    assert {"api_key", "secret_key", "api_key_previous"}.isdisjoint(columns)
    assert row["api_key_hash"] == hash_api_key("legacy-api")
    assert row["secret_key_hash"] == hash_api_key("legacy-secret")
    assert secret_box.decrypt(str(row["secret_key_encrypted"])) == "legacy-secret"
    assert row["api_key_previous_hash"] == hash_api_key("legacy-previous")


@pytest.mark.asyncio
async def test_worker_credential_upgrade_rejects_partial_plaintext_schema(pg_connection) -> None:
    await pg_connection.execute("CREATE TABLE public.workers (id BIGINT PRIMARY KEY, api_key TEXT, secret_key TEXT)")

    with pytest.raises(RuntimeError, match="明文凭据列不完整"):
        async with pg_connection.transaction():
            await migrate_worker_credentials(_ConnectionAdapter(pg_connection))


@pytest.mark.asyncio
async def test_worker_hmac_encryption_key_rotation_commits_primary_only_ciphertexts(pg_connection, monkeypatch) -> None:
    primary_key = Fernet.generate_key()
    legacy_key = Fernet.generate_key()
    hmac_secret = "integration-worker-hmac"
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", primary_key.decode("ascii"))
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS_LEGACY", legacy_key.decode("ascii"))
    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_ALLOW_LEGACY_SHA256", False)
    secret_box._cached = None
    secret_box._cache_key = None
    legacy = Fernet(legacy_key)
    await pg_connection.execute(
        """
        CREATE TABLE public.workers (
            id BIGINT PRIMARY KEY,
            public_id VARCHAR(32) NOT NULL UNIQUE,
            secret_key_hash VARCHAR(128),
            secret_key_encrypted TEXT,
            redis_username VARCHAR(80),
            redis_password_encrypted TEXT
        )
        """
    )
    await pg_connection.execute(
        "INSERT INTO public.workers VALUES ($1, $2, $3, $4, $5, $6)",
        1,
        "worker-integration",
        hash_api_key(hmac_secret),
        legacy.encrypt(hmac_secret.encode()).decode(),
        "worker_integration",
        legacy.encrypt(b"redis-password").decode(),
    )
    adapter = _ConnectionAdapter(pg_connection)

    async with pg_connection.transaction():
        result = await rotate_worker_credentials(adapter, apply=True)

    assert result.rows_rewritten == 1
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS_LEGACY", "")
    secret_box._cached = None
    secret_box._cache_key = None
    async with pg_connection.transaction():
        assert await verify_worker_credentials_primary_only(adapter) == 1
