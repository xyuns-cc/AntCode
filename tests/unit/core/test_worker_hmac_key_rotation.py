from __future__ import annotations

from copy import deepcopy

import pytest
from antcode_core.application.services.workers.worker_hmac_key_rotation import (
    rotate_worker_credentials,
    verify_worker_credentials_primary_only,
)
from antcode_core.common.config import settings
from antcode_core.common.security.api_key import hash_api_key
from antcode_core.common.security.secret_box import SecretBox
from cryptography.fernet import Fernet, InvalidToken

EXPECTED_CREDENTIAL_COUNT = 2


class _Connection:
    def __init__(self, rows: list[dict], *, update_affected: int = 1) -> None:
        self.rows = deepcopy(rows)
        self.update_affected = update_affected
        self.lock_count = 0
        self.updates: list[list[object]] = []

    async def execute_query_dict(self, _sql: str):
        return deepcopy(self.rows)

    async def execute_query(self, sql: str, params: list[object] | None = None):
        if sql.startswith("LOCK TABLE"):
            self.lock_count += 1
            return 0, []
        if not sql.startswith('UPDATE public."workers"'):
            raise AssertionError(f"unexpected query: {sql}")
        values = params or []
        self.updates.append(values)
        if self.update_affected == 1:
            row = next(item for item in self.rows if item["id"] == values[2])
            row["secret_key_encrypted"] = values[0]
            row["redis_password_encrypted"] = values[1]
        return self.update_affected, []


def _box(monkeypatch) -> tuple[SecretBox, Fernet, Fernet]:
    primary_key = Fernet.generate_key()
    legacy_key = Fernet.generate_key()
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", primary_key.decode("ascii"))
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS_LEGACY", legacy_key.decode("ascii"))
    return SecretBox(), Fernet(primary_key), Fernet(legacy_key)


def _row(legacy: Fernet, *, secret: str = "worker-hmac") -> dict:
    return {
        "id": 7,
        "public_id": "worker-7",
        "secret_key_hash": hash_api_key(secret),
        "secret_key_encrypted": legacy.encrypt(secret.encode()).decode(),
        "redis_username": "worker_worker7",
        "redis_password_encrypted": legacy.encrypt(b"redis-password").decode(),
    }


@pytest.mark.asyncio
async def test_dry_run_validates_without_writing(monkeypatch) -> None:
    box, _primary, legacy = _box(monkeypatch)
    connection = _Connection([_row(legacy)])

    result = await rotate_worker_credentials(connection, apply=False, box=box)

    assert result.workers_scanned == 1
    assert result.hmac_secrets_scanned == 1
    assert result.redis_passwords_scanned == 1
    assert result.ciphertexts_requiring_rotation == EXPECTED_CREDENTIAL_COUNT
    assert result.rows_rewritten == 0
    assert connection.lock_count == 1
    assert connection.updates == []


@pytest.mark.asyncio
async def test_apply_rotates_both_worker_credentials_and_is_idempotent(monkeypatch) -> None:
    box, _primary, legacy = _box(monkeypatch)
    connection = _Connection([_row(legacy)])

    first = await rotate_worker_credentials(connection, apply=True, box=box)
    second = await rotate_worker_credentials(connection, apply=True, box=box)

    assert first.ciphertexts_requiring_rotation == EXPECTED_CREDENTIAL_COUNT
    assert first.rows_rewritten == 1
    assert second.ciphertexts_requiring_rotation == 0
    assert second.rows_rewritten == 0
    assert len(connection.updates) == 1
    assert box.decrypt_primary(connection.rows[0]["secret_key_encrypted"]) == "worker-hmac"
    assert box.decrypt_primary(connection.rows[0]["redis_password_encrypted"]) == "redis-password"


@pytest.mark.asyncio
async def test_integrity_mismatch_aborts_before_any_write(monkeypatch) -> None:
    box, _primary, legacy = _box(monkeypatch)
    row = _row(legacy)
    row["secret_key_hash"] = hash_api_key("different")
    connection = _Connection([row])

    with pytest.raises(RuntimeError, match="完整性校验失败"):
        await rotate_worker_credentials(connection, apply=True, box=box)

    assert connection.updates == []


@pytest.mark.asyncio
async def test_incomplete_hmac_state_is_rejected(monkeypatch) -> None:
    box, _primary, _legacy = _box(monkeypatch)
    row = {
        "id": 9,
        "public_id": "worker-9",
        "secret_key_hash": hash_api_key("missing-ciphertext"),
        "secret_key_encrypted": None,
        "redis_username": None,
        "redis_password_encrypted": None,
    }

    with pytest.raises(RuntimeError, match="凭据不完整"):
        await rotate_worker_credentials(_Connection([row]), apply=False, box=box)


@pytest.mark.asyncio
async def test_unknown_key_ciphertext_is_not_silently_skipped(monkeypatch) -> None:
    box, _primary, _legacy = _box(monkeypatch)
    unknown = Fernet(Fernet.generate_key())
    connection = _Connection([_row(unknown)])

    with pytest.raises(InvalidToken):
        await rotate_worker_credentials(connection, apply=False, box=box)


@pytest.mark.asyncio
async def test_primary_only_verification_rejects_legacy_then_accepts_rotated(monkeypatch) -> None:
    box, _primary, legacy = _box(monkeypatch)
    connection = _Connection([_row(legacy)])

    with pytest.raises(InvalidToken):
        await verify_worker_credentials_primary_only(connection, box=box)

    await rotate_worker_credentials(connection, apply=True, box=box)
    assert await verify_worker_credentials_primary_only(connection, box=box) == 1


@pytest.mark.asyncio
async def test_unexpected_update_count_aborts_transaction(monkeypatch) -> None:
    box, _primary, legacy = _box(monkeypatch)
    connection = _Connection([_row(legacy)], update_affected=0)

    with pytest.raises(RuntimeError, match="affected=0"):
        await rotate_worker_credentials(connection, apply=True, box=box)


@pytest.mark.asyncio
async def test_incomplete_redis_acl_state_is_rejected(monkeypatch) -> None:
    box, _primary, legacy = _box(monkeypatch)
    row = _row(legacy)
    row["redis_password_encrypted"] = None

    with pytest.raises(RuntimeError, match="Redis ACL 凭据不完整"):
        await rotate_worker_credentials(_Connection([row]), apply=False, box=box)
