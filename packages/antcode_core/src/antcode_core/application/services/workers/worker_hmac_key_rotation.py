"""Transactional rotation of persisted Worker credentials."""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from antcode_core.common.security.api_key import hash_api_key
from antcode_core.common.security.secret_box import SecretBox, secret_box

_WORKER_TABLE_LOCK_SQL = 'LOCK TABLE public."workers" IN SHARE ROW EXCLUSIVE MODE'
_SELECT_SQL = (
    'SELECT "id", "public_id", "secret_key_hash", "secret_key_encrypted", '
    '"redis_username", "redis_password_encrypted" FROM public."workers" '
    'WHERE "secret_key_hash" IS NOT NULL OR "secret_key_encrypted" IS NOT NULL '
    'OR "redis_username" IS NOT NULL OR "redis_password_encrypted" IS NOT NULL ORDER BY "id"'
)
_UPDATE_SQL = 'UPDATE public."workers" SET "secret_key_encrypted"=$1, "redis_password_encrypted"=$2 WHERE "id"=$3'


@dataclass(frozen=True)
class WorkerCredentialRotationResult:
    workers_scanned: int
    hmac_secrets_scanned: int
    redis_passwords_scanned: int
    ciphertexts_requiring_rotation: int
    rows_rewritten: int


def _decrypt_hmac(row: dict, box: SecretBox) -> str | None:
    ciphertext = row.get("secret_key_encrypted")
    secret_hash = row.get("secret_key_hash")
    worker_id = str(row["public_id"])
    if ciphertext is None and secret_hash is None:
        return None
    if not ciphertext or not secret_hash:
        raise RuntimeError(f"Worker HMAC 凭据不完整: worker={worker_id}")
    plaintext = box.decrypt(str(ciphertext))
    if not hmac.compare_digest(hash_api_key(plaintext), str(secret_hash)):
        raise RuntimeError(f"Worker HMAC secret 完整性校验失败: worker={worker_id}")
    return plaintext


def _verify_primary_hmac(row: dict, ciphertext: str, box: SecretBox) -> None:
    plaintext = box.decrypt_primary(ciphertext)
    if not hmac.compare_digest(hash_api_key(plaintext), str(row["secret_key_hash"])):
        raise RuntimeError(f"Worker HMAC 主密钥完整性校验失败: worker={row['public_id']}")


def _verify_redis_acl_state(row: dict) -> None:
    username = row.get("redis_username")
    ciphertext = row.get("redis_password_encrypted")
    if bool(username) != bool(ciphertext):
        raise RuntimeError(f"Worker Redis ACL 凭据不完整: worker={row['public_id']}")


def _rotate_ciphertext(ciphertext: object, box: SecretBox, *, apply: bool) -> tuple[str | None, bool]:
    if ciphertext is None:
        return None, False
    value = str(ciphertext)
    requires_rotation = box.needs_rotation(value)
    return (box.rotate(value) if apply and requires_rotation else value), requires_rotation


def _prepare_row(row: dict, box: SecretBox, *, apply: bool) -> tuple[str | None, str | None, int]:
    hmac_plaintext = _decrypt_hmac(row, box)
    _verify_redis_acl_state(row)
    rotated_hmac, hmac_requires_rotation = _rotate_ciphertext(row.get("secret_key_encrypted"), box, apply=apply)
    rotated_redis, redis_requires_rotation = _rotate_ciphertext(row.get("redis_password_encrypted"), box, apply=apply)
    if apply and hmac_plaintext is not None:
        verified = box.decrypt_primary(rotated_hmac or "")
        if not hmac.compare_digest(verified, hmac_plaintext):
            raise RuntimeError(f"Worker HMAC 主密钥复验失败: worker={row['public_id']}")
    if apply and rotated_redis is not None:
        box.decrypt_primary(rotated_redis)
    return rotated_hmac, rotated_redis, int(hmac_requires_rotation) + int(redis_requires_rotation)


async def _verify_persisted_rows(
    connection, expected: dict[int, tuple[str | None, str | None]], box: SecretBox
) -> None:
    rows = await connection.execute_query_dict(_SELECT_SQL)
    actual_ids = {int(row["id"]) for row in rows}
    if actual_ids != set(expected):
        raise RuntimeError("Worker 凭据轮换期间数据库行集合发生变化")
    for row in rows:
        row_id = int(row["id"])
        actual = (row.get("secret_key_encrypted"), row.get("redis_password_encrypted"))
        if actual != expected[row_id]:
            raise RuntimeError(f"Worker 凭据轮换写入后复读不一致: worker={row['public_id']}")
        _decrypt_hmac(row, box)
        _verify_redis_acl_state(row)
        if actual[0] is not None:
            _verify_primary_hmac(row, str(actual[0]), box)
        if actual[1] is not None:
            box.decrypt_primary(str(actual[1]))


def _row_changed(row: dict, expected: tuple[str | None, str | None]) -> bool:
    persisted = (row.get("secret_key_encrypted"), row.get("redis_password_encrypted"))
    return expected != persisted


async def rotate_worker_credentials(
    connection,
    *,
    apply: bool,
    box: SecretBox = secret_box,
    acquire_table_lock: bool = True,
) -> WorkerCredentialRotationResult:
    """Validate or rotate all Worker HMAC and Redis ACL ciphertexts atomically."""
    if acquire_table_lock:
        await connection.execute_query(_WORKER_TABLE_LOCK_SQL)
    rows = await connection.execute_query_dict(_SELECT_SQL)
    expected: dict[int, tuple[str | None, str | None]] = {}
    requiring_rotation = 0
    for row in rows:
        rotated_hmac, rotated_redis, count = _prepare_row(row, box, apply=apply)
        expected[int(row["id"])] = (rotated_hmac, rotated_redis)
        requiring_rotation += count
    rows_rewritten = sum(_row_changed(row, expected[int(row["id"])]) for row in rows) if apply else 0
    if apply:
        await _write_rows(connection, rows, expected)
        await _verify_persisted_rows(connection, expected, box)
    return WorkerCredentialRotationResult(
        workers_scanned=len(rows),
        hmac_secrets_scanned=sum(row.get("secret_key_encrypted") is not None for row in rows),
        redis_passwords_scanned=sum(row.get("redis_password_encrypted") is not None for row in rows),
        ciphertexts_requiring_rotation=requiring_rotation,
        rows_rewritten=rows_rewritten,
    )


async def _write_rows(connection, rows: list[dict], expected: dict[int, tuple[str | None, str | None]]) -> None:
    for row in rows:
        row_id = int(row["id"])
        values = expected[row_id]
        if values == (row.get("secret_key_encrypted"), row.get("redis_password_encrypted")):
            continue
        affected, _ = await connection.execute_query(_UPDATE_SQL, [values[0], values[1], row_id])
        if int(affected) != 1:
            raise RuntimeError(f"Worker 凭据轮换更新行数异常: worker={row['public_id']}, affected={affected}")


async def verify_worker_credentials_primary_only(
    connection,
    *,
    box: SecretBox = secret_box,
    acquire_table_lock: bool = True,
) -> int:
    """Fail unless every Worker credential decrypts with the current primary key."""
    if acquire_table_lock:
        await connection.execute_query(_WORKER_TABLE_LOCK_SQL)
    rows = await connection.execute_query_dict(_SELECT_SQL)
    for row in rows:
        _decrypt_hmac(row, box)
        _verify_redis_acl_state(row)
        hmac_ciphertext = row.get("secret_key_encrypted")
        if hmac_ciphertext is not None:
            _verify_primary_hmac(row, str(hmac_ciphertext), box)
        redis_ciphertext = row.get("redis_password_encrypted")
        if redis_ciphertext is not None:
            box.decrypt_primary(str(redis_ciphertext))
    return len(rows)


__all__ = [
    "WorkerCredentialRotationResult",
    "rotate_worker_credentials",
    "verify_worker_credentials_primary_only",
]
