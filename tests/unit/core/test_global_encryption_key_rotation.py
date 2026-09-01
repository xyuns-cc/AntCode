from __future__ import annotations

import json
from copy import deepcopy

import pytest
from antcode_core.application.services.security.encryption_rotation_contract import (
    GLOBAL_ENCRYPTED_TABLES,
    EncryptedTableSpec,
    validate_rotation_contract,
)
from antcode_core.application.services.security.postgres_encryption_key_rotation import (
    rotate_postgres_ciphertexts,
    verify_postgres_ciphertexts_primary_only,
)
from antcode_core.application.services.security.redispatch_rotation_guard import (
    inspect_redispatch_drain,
    require_redispatch_drained,
)
from antcode_core.common.config import settings
from antcode_core.common.security.api_key import hash_api_key
from antcode_core.common.security.encrypted_fields import EncryptedJSONField, EncryptedTextField
from antcode_core.common.security.secret_box import SecretBox
from antcode_core.domain.models import ProjectCode, ProjectFile, ProjectRule, SystemConfig, Task
from cryptography.fernet import Fernet, InvalidToken

EXPECTED_NON_EMPTY_REDISPATCH_ENTRIES = 6

#: 与 ``settings.APP_TITLE`` 默认值一致——非 ASCII 不是构造出来的边角输入。
NON_ASCII_CONFIG = "AntCode 任务调度平台"


class _Connection:
    def __init__(
        self,
        rows: dict[str, list[dict]],
        *,
        fail_after_update: bool = False,
        rotation_lock_acquired: bool = True,
    ) -> None:
        self.rows = deepcopy(rows)
        self.statements: list[tuple[str, list[object]]] = []
        self.fail_after_update = fail_after_update
        self.rotation_lock_acquired = rotation_lock_acquired

    async def execute_query_dict(self, sql: str, params: list[object] | None = None):
        del params
        if "pg_try_advisory_xact_lock" in sql:
            self.statements.append((sql, []))
            return [{"acquired": self.rotation_lock_acquired}]
        table = _table_name(sql)
        if self.fail_after_update and any(statement.startswith("UPDATE") for statement, _ in self.statements):
            raise ConnectionError("post-write read failed")
        return deepcopy(self.rows.get(table, []))

    async def execute_query(self, sql: str, params: list[object] | None = None):
        values = list(params or [])
        self.statements.append((sql, values))
        if not sql.startswith("UPDATE"):
            return 0, []
        table = _table_name(sql)
        row = next(item for item in self.rows[table] if item["id"] == values[-1])
        assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0].split(", ")
        for assignment, value in zip(assignments, values[:-1], strict=True):
            row[assignment.split('"')[1]] = value
        return 1, []


class _Redis:
    def __init__(self, counts: dict[tuple[str, str], int]) -> None:
        self.counts = counts

    async def zcard(self, key: str) -> int:
        return self.counts.get(("zset", key), 0)

    async def hlen(self, key: str) -> int:
        return self.counts.get(("hash", key), 0)


def _table_name(sql: str) -> str:
    marker = 'public."'
    return sql.split(marker, 1)[1].split('"', 1)[0]


def _box(monkeypatch) -> tuple[SecretBox, Fernet]:
    primary = Fernet.generate_key()
    legacy = Fernet.generate_key()
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", primary.decode())
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS_LEGACY", legacy.decode())
    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "")
    return SecretBox(), Fernet(legacy)


def _json(legacy: Fernet, value: str) -> str:
    token = legacy.encrypt(value.encode()).decode()
    return json.dumps({"__antcode_encrypted_v1__": token})


def _rows(legacy: Fernet) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = {}
    for spec in GLOBAL_ENCRYPTED_TABLES:
        row: dict[str, object] = {"id": 1}
        for field in spec.fields:
            plaintext = f"{spec.table}-{field.column}"
            if field.storage_format.value == "json-v1":
                plaintext = json.dumps({"value": plaintext})
            token = legacy.encrypt(plaintext.encode()).decode()
            if field.storage_format.value == "text-v1":
                row[field.column] = "enc:v1:" + token
            elif field.storage_format.value == "json-v1":
                row[field.column] = json.dumps({"__antcode_encrypted_v1__": token})
            else:
                row[field.column] = token
        rows[spec.table] = [row]
    secret = "worker-hmac-secret"
    rows["workers"] = [
        {
            "id": 9,
            "public_id": "worker-9",
            "secret_key_hash": hash_api_key(secret),
            "secret_key_encrypted": legacy.encrypt(secret.encode()).decode(),
            "redis_username": "worker_9",
            "redis_password_encrypted": legacy.encrypt(b"redis-password").decode(),
        }
    ]
    return rows


@pytest.mark.asyncio
async def test_dry_run_covers_every_ciphertext_without_writes(monkeypatch) -> None:
    box, legacy = _box(monkeypatch)
    connection = _Connection(_rows(legacy))

    result = await rotate_postgres_ciphertexts(connection, apply=False, box=box)

    assert {item.table for item in result.tables} == {item.table for item in GLOBAL_ENCRYPTED_TABLES}
    assert result.ciphertexts_scanned == result.ciphertexts_requiring_rotation
    assert result.rows_rewritten == 0
    assert not any(statement.startswith("UPDATE") for statement, _ in connection.statements)
    assert any("pg_try_advisory_xact_lock" in statement for statement, _ in connection.statements)
    assert not any(statement.startswith("LOCK TABLE") for statement, _ in connection.statements)
    lock_config_index = next(
        index
        for index, (statement, _params) in enumerate(connection.statements)
        if "set_config('lock_timeout'" in statement
    )
    advisory_index = next(
        index
        for index, (statement, _params) in enumerate(connection.statements)
        if "pg_try_advisory_xact_lock" in statement
    )
    assert lock_config_index < advisory_index
    assert any("set_config('statement_timeout'" in statement for statement, _ in connection.statements)


def test_rotation_contract_declares_every_encrypted_table_once(monkeypatch) -> None:
    validate_rotation_contract()
    import antcode_core.application.services.security.encryption_rotation_contract as contract

    monkeypatch.setattr(
        contract, "GLOBAL_ENCRYPTED_TABLES", (*GLOBAL_ENCRYPTED_TABLES, EncryptedTableSpec("extra", ()))
    )
    with pytest.raises(RuntimeError, match="清单不完整"):
        validate_rotation_contract()


def test_rotation_contract_matches_every_orm_encrypted_field() -> None:
    models = (ProjectFile, ProjectCode, ProjectRule, Task, SystemConfig)
    actual = {
        (model._meta.db_table, name)
        for model in models
        for name, field in model._meta.fields_map.items()
        if isinstance(field, (EncryptedJSONField, EncryptedTextField))
    }
    expected = {(spec.table, field.column) for spec in GLOBAL_ENCRYPTED_TABLES for field in spec.fields}
    assert actual == expected.difference({("git_credentials", "secret_encrypted")})


@pytest.mark.asyncio
async def test_concurrent_rotation_is_rejected_without_waiting(monkeypatch) -> None:
    box, legacy = _box(monkeypatch)

    with pytest.raises(RuntimeError, match="正在运行"):
        await rotate_postgres_ciphertexts(
            _Connection(_rows(legacy), rotation_lock_acquired=False),
            apply=False,
            box=box,
        )


@pytest.mark.asyncio
async def test_apply_rotates_all_formats_and_primary_only_verifies(monkeypatch) -> None:
    box, legacy = _box(monkeypatch)
    connection = _Connection(_rows(legacy))

    first = await rotate_postgres_ciphertexts(connection, apply=True, box=box)
    second = await rotate_postgres_ciphertexts(connection, apply=True, box=box)
    verified = await verify_postgres_ciphertexts_primary_only(connection, box=box)

    assert first.rows_rewritten == len(GLOBAL_ENCRYPTED_TABLES) + 1
    assert second.ciphertexts_requiring_rotation == 0
    assert second.rows_rewritten == 0
    assert verified.ciphertexts_requiring_rotation == 0
    assert any(statement.startswith("LOCK TABLE") for statement, _ in connection.statements)


@pytest.mark.asyncio
async def test_plaintext_or_unknown_ciphertext_fails_before_write(monkeypatch) -> None:
    box, legacy = _box(monkeypatch)
    rows = _rows(legacy)
    rows["system_configs"][0]["config_value"] = "plaintext"
    connection = _Connection(rows)

    with pytest.raises(RuntimeError, match="密文格式"):
        await rotate_postgres_ciphertexts(connection, apply=True, box=box)

    assert not any(statement.startswith("UPDATE") for statement, _ in connection.statements)


@pytest.mark.asyncio
async def test_rotation_handles_non_ascii_plaintext(monkeypatch) -> None:
    """密文里存的是任意用户数据，非 ASCII 明文必须能轮换。

    这不是假想输入：``APP_TITLE`` 的默认值就是 ``"AntCode 任务调度平台"``，会被种进
    ``system_configs``。修复前 ``hmac.compare_digest`` 收到非 ASCII ``str`` 直接抛
    ``TypeError``，于是 ``deploy-production.sh rotate-encryption-key`` 在**每个默认
    安装**上都死在 dry-run 阶段。

    证伪方式：把 ``_same_plaintext`` 里的 ``.encode("utf-8")`` 去掉，这条即变红。
    """
    box, legacy = _box(monkeypatch)
    rows = _rows(legacy)
    rows["system_configs"][0]["config_value"] = "enc:v1:" + legacy.encrypt(NON_ASCII_CONFIG.encode()).decode()
    connection = _Connection(rows)

    report = await rotate_postgres_ciphertexts(connection, apply=True, box=box)
    verified = await verify_postgres_ciphertexts_primary_only(connection, box=box)

    assert report.rows_rewritten == len(GLOBAL_ENCRYPTED_TABLES) + 1
    assert verified.ciphertexts_requiring_rotation == 0


@pytest.mark.asyncio
async def test_worker_integrity_failure_aborts_before_any_table_write(monkeypatch) -> None:
    box, legacy = _box(monkeypatch)
    rows = _rows(legacy)
    rows["workers"][0]["secret_key_hash"] = hash_api_key("different")
    connection = _Connection(rows)

    with pytest.raises(RuntimeError, match="完整性校验失败"):
        await rotate_postgres_ciphertexts(connection, apply=True, box=box)

    assert not any(statement.startswith("UPDATE") for statement, _ in connection.statements)


@pytest.mark.asyncio
async def test_primary_only_rejects_legacy_ciphertext(monkeypatch) -> None:
    box, legacy = _box(monkeypatch)

    with pytest.raises(InvalidToken):
        await verify_postgres_ciphertexts_primary_only(_Connection(_rows(legacy)), box=box)


@pytest.mark.asyncio
async def test_authenticated_non_json_plaintext_fails_before_write(monkeypatch) -> None:
    box, legacy = _box(monkeypatch)
    rows = _rows(legacy)
    rows["project_files"][0]["runtime_config"] = _json(legacy, "not-json")
    connection = _Connection(rows)

    with pytest.raises(RuntimeError, match="JSON 字段明文合同无效"):
        await rotate_postgres_ciphertexts(connection, apply=True, box=box)

    assert not any(statement.startswith("UPDATE") for statement, _ in connection.statements)


@pytest.mark.asyncio
async def test_authenticated_json_scalar_fails_before_write(monkeypatch) -> None:
    box, legacy = _box(monkeypatch)
    rows = _rows(legacy)
    rows["project_files"][0]["runtime_config"] = _json(legacy, "null")
    connection = _Connection(rows)

    with pytest.raises(RuntimeError, match="必须是 object"):
        await rotate_postgres_ciphertexts(connection, apply=True, box=box)

    assert not any(statement.startswith("UPDATE") for statement, _ in connection.statements)


@pytest.mark.asyncio
async def test_post_write_read_failure_is_not_silently_accepted(monkeypatch) -> None:
    box, legacy = _box(monkeypatch)
    connection = _Connection(_rows(legacy), fail_after_update=True)

    with pytest.raises(ConnectionError, match="post-write read failed"):
        await rotate_postgres_ciphertexts(connection, apply=True, box=box)


@pytest.mark.asyncio
async def test_redispatch_guard_checks_current_and_legacy_keys() -> None:
    namespace = "tenant-a"
    current = "{tenant-a}:task:redispatch"
    legacy = "tenant-a:task:redispatch"
    state = await inspect_redispatch_drain(
        _Redis({("zset", current): 2, ("hash", f"{current}:processing"): 1, ("zset", legacy): 3}),
        namespace,
    )

    assert state.total == EXPECTED_NON_EMPTY_REDISPATCH_ENTRIES
    with pytest.raises(RuntimeError, match="禁止轮换"):
        require_redispatch_drained(state)


@pytest.mark.asyncio
async def test_redispatch_guard_accepts_only_fully_drained_queue() -> None:
    state = await inspect_redispatch_drain(_Redis({}), "tenant-a")
    require_redispatch_drained(state)
    assert state.total == 0
