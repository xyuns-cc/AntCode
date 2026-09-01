"""Transactional rotation for every PostgreSQL SecretBox ciphertext."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field

from cryptography.fernet import InvalidToken

from antcode_core.application.services.security.encryption_rotation_contract import (
    GLOBAL_ENCRYPTED_TABLES,
    CiphertextFormat,
    EncryptedFieldSpec,
    EncryptedTableSpec,
    TableRotationResult,
    decode_ciphertext,
    encode_ciphertext,
    validate_rotation_contract,
)
from antcode_core.application.services.workers.worker_hmac_key_rotation import (
    WorkerCredentialRotationResult,
    rotate_worker_credentials,
    verify_worker_credentials_primary_only,
)
from antcode_core.common.security.secret_box import SecretBox, secret_box

ROTATION_LOCK_NAME = "antcode:encryption-key:global-rotation:v1"
_LOCK_TIMEOUT = "30s"
_STATEMENT_TIMEOUT = "30min"
_ALL_TABLES = tuple(spec.table for spec in GLOBAL_ENCRYPTED_TABLES) + ("workers",)
_TABLE_LOCK_SQL = "LOCK TABLE " + ", ".join(f'public."{name}"' for name in _ALL_TABLES)
_TABLE_LOCK_SQL += " IN SHARE ROW EXCLUSIVE MODE"
_ADVISORY_TRY_LOCK_SQL = "SELECT pg_try_advisory_xact_lock(hashtextextended($1, 0)) AS acquired"


@dataclass(frozen=True)
class PostgresRotationResult:
    tables: tuple[TableRotationResult, ...]
    workers: WorkerCredentialRotationResult

    @property
    def ciphertexts_scanned(self) -> int:
        regular = sum(item.ciphertexts_scanned for item in self.tables)
        return regular + self.workers.hmac_secrets_scanned + self.workers.redis_passwords_scanned

    @property
    def ciphertexts_requiring_rotation(self) -> int:
        regular = sum(item.ciphertexts_requiring_rotation for item in self.tables)
        return regular + self.workers.ciphertexts_requiring_rotation

    @property
    def rows_rewritten(self) -> int:
        return sum(item.rows_rewritten for item in self.tables) + self.workers.rows_rewritten


@dataclass(frozen=True)
class _FieldPlan:
    spec: EncryptedFieldSpec
    token: str = field(repr=False)
    db_value: str = field(repr=False)
    plaintext_digest: bytes = field(repr=False)
    requires_rotation: bool
    changed: bool


@dataclass(frozen=True)
class _RowPlan:
    row_id: int
    fields: tuple[_FieldPlan | None, ...]


@dataclass(frozen=True)
class _TablePlan:
    spec: EncryptedTableSpec
    rows: tuple[_RowPlan, ...]


async def rotate_postgres_ciphertexts(
    connection,
    *,
    apply: bool,
    box: SecretBox = secret_box,
) -> PostgresRotationResult:
    """Validate or rotate all PostgreSQL ciphertexts in the caller transaction."""
    await _lock_all_tables(connection, apply=apply)
    plans = tuple([await _plan_table(connection, spec, box) for spec in GLOBAL_ENCRYPTED_TABLES])
    workers = await rotate_worker_credentials(connection, apply=False, box=box, acquire_table_lock=False)
    if apply:
        for plan in plans:
            await _write_table(connection, plan)
        for plan in plans:
            await _verify_persisted_table(connection, plan, box)
        workers = await rotate_worker_credentials(connection, apply=True, box=box, acquire_table_lock=False)
    return PostgresRotationResult(tuple(_result(plan, apply=apply) for plan in plans), workers)


async def verify_postgres_ciphertexts_primary_only(
    connection,
    *,
    box: SecretBox = secret_box,
) -> PostgresRotationResult:
    """Fail unless all PostgreSQL ciphertexts use the current primary key."""
    await _lock_all_tables(connection, apply=True)
    plans = tuple([await _plan_table(connection, spec, box, primary_only=True) for spec in GLOBAL_ENCRYPTED_TABLES])
    workers = await rotate_worker_credentials(connection, apply=False, box=box, acquire_table_lock=False)
    await verify_worker_credentials_primary_only(connection, box=box, acquire_table_lock=False)
    return PostgresRotationResult(tuple(_result(plan, apply=False) for plan in plans), workers)


async def _lock_all_tables(connection, *, apply: bool) -> None:
    validate_rotation_contract()
    await connection.execute_query("SELECT set_config('lock_timeout', $1, true)", [_LOCK_TIMEOUT])
    await connection.execute_query("SELECT set_config('statement_timeout', $1, true)", [_STATEMENT_TIMEOUT])
    rows = await connection.execute_query_dict(_ADVISORY_TRY_LOCK_SQL, [ROTATION_LOCK_NAME])
    if len(rows) != 1 or rows[0].get("acquired") is not True:
        raise RuntimeError("已有全域密钥轮换命令正在运行")
    if apply:
        await connection.execute_query(_TABLE_LOCK_SQL)


async def _plan_table(
    connection,
    spec: EncryptedTableSpec,
    box: SecretBox,
    *,
    primary_only: bool = False,
) -> _TablePlan:
    rows = await _read_rows(connection, spec)
    plans = tuple(_plan_row(row, spec, box, primary_only=primary_only) for row in rows)
    return _TablePlan(spec, plans)


def _plan_row(row: dict, spec: EncryptedTableSpec, box: SecretBox, *, primary_only: bool) -> _RowPlan:
    row_id = int(row["id"])
    fields = tuple(
        _plan_field(
            row.get(item.column),
            item,
            box,
            context=f"{spec.table}.{item.column} row={row_id}",
            primary_only=primary_only,
        )
        for item in spec.fields
    )
    return _RowPlan(row_id, fields)


def _plan_field(
    value: object,
    spec: EncryptedFieldSpec,
    box: SecretBox,
    *,
    context: str,
    primary_only: bool,
) -> _FieldPlan | None:
    if value is None:
        return None
    token = decode_ciphertext(value, spec, context=context)
    plaintext = box.decrypt_primary(token) if primary_only else box.decrypt(token)
    _validate_plaintext_contract(plaintext, spec, context=context)
    requires_rotation = False if primary_only else _requires_rotation(token, plaintext, box)
    rotated = box.rotate(token) if requires_rotation else token
    _verify_plaintext(rotated, plaintext, box, context=context)
    return _FieldPlan(
        spec=spec,
        token=rotated,
        db_value=encode_ciphertext(rotated, spec),
        plaintext_digest=_digest(plaintext),
        requires_rotation=requires_rotation,
        changed=rotated != token,
    )


def _same_plaintext(left: str, right: str) -> bool:
    """比对两段解密明文。

    必须先 encode：``hmac.compare_digest`` 对 ``str`` 只接受纯 ASCII，非 ASCII 抛
    ``TypeError``。而这里比的是任意用户数据——``init_db`` 就会种一行
    ``system_configs.app_title = "AntCode 任务调度平台"``，于是密钥轮换在每个
    默认安装上都会在 dry-run 阶段炸掉。bytes 比对同样是常量时间。
    """
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _requires_rotation(token: str, plaintext: str, box: SecretBox) -> bool:
    try:
        primary_plaintext = box.decrypt_primary(token)
    except InvalidToken:
        return True
    if not _same_plaintext(primary_plaintext, plaintext):
        raise RuntimeError("主密钥与 keyring 解密结果不一致")
    return False


def _verify_plaintext(token: str, plaintext: str, box: SecretBox, *, context: str) -> None:
    if not _same_plaintext(box.decrypt_primary(token), plaintext):
        raise RuntimeError(f"密文轮换主密钥复验失败: {context}")


def _validate_plaintext_contract(plaintext: str, spec: EncryptedFieldSpec, *, context: str) -> None:
    if spec.storage_format is not CiphertextFormat.JSON_V1:
        return
    try:
        decoded = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"加密 JSON 字段明文合同无效: {context}") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"加密 JSON 字段明文必须是 object: {context}")


async def _write_table(connection, plan: _TablePlan) -> None:
    for row in plan.rows:
        changed = tuple(item for item in row.fields if item is not None and item.changed)
        if not changed:
            continue
        affected, _ = await connection.execute_query(
            _update_sql(plan.spec.table, changed), _update_values(row, changed)
        )
        if int(affected) != 1:
            raise RuntimeError(f"密文轮换更新行数异常: table={plan.spec.table} row={row.row_id} affected={affected}")


async def _verify_persisted_table(connection, plan: _TablePlan, box: SecretBox) -> None:
    rows = await _read_rows(connection, plan.spec)
    actual = {int(row["id"]): row for row in rows}
    expected = {row.row_id: row for row in plan.rows}
    if set(actual) != set(expected):
        raise RuntimeError(f"密文轮换期间数据库行集合发生变化: table={plan.spec.table}")
    for row_id, row_plan in expected.items():
        _verify_row(actual[row_id], row_plan, spec=plan.spec, box=box)


def _verify_row(
    row: dict,
    plan: _RowPlan,
    *,
    spec: EncryptedTableSpec,
    box: SecretBox,
) -> None:
    for field_spec, expected in zip(spec.fields, plan.fields, strict=True):
        value = row.get(field_spec.column)
        context = f"{spec.table}.{field_spec.column} row={plan.row_id}"
        if expected is None:
            if value is not None:
                raise RuntimeError(f"密文轮换写入后复读不一致: {context}")
            continue
        token = decode_ciphertext(value, field_spec, context=context)
        if token != expected.token or not hmac.compare_digest(
            _digest(box.decrypt_primary(token)), expected.plaintext_digest
        ):
            raise RuntimeError(f"密文轮换写入后复读不一致: {context}")


def _select_sql(spec: EncryptedTableSpec) -> str:
    columns = ", ".join([f'"{item.column}"' for item in spec.fields])
    # Identifiers come only from immutable GLOBAL_ENCRYPTED_TABLES, never external input.
    return f'SELECT "id", {columns} FROM public."{spec.table}" ORDER BY "id"'  # nosec B608


async def _read_rows(connection, spec: EncryptedTableSpec) -> list[dict]:
    rows = await connection.execute_query_dict(_select_sql(spec))
    return [dict(row) for row in rows]


def _update_sql(table: str, fields: tuple[_FieldPlan, ...]) -> str:
    assignments = []
    for index, item in enumerate(fields, start=1):
        cast_suffix = "::jsonb" if item.spec.storage_format is CiphertextFormat.JSON_V1 else ""
        assignments.append(f'"{item.spec.column}"=${index}{cast_suffix}')
    # Identifiers come only from immutable GLOBAL_ENCRYPTED_TABLES; values stay parameterized.
    return f'UPDATE public."{table}" SET {", ".join(assignments)} WHERE "id"=${len(fields) + 1}'  # nosec B608


def _update_values(row: _RowPlan, fields: tuple[_FieldPlan, ...]) -> list[object]:
    return [*[item.db_value for item in fields], row.row_id]


def _result(plan: _TablePlan, *, apply: bool) -> TableRotationResult:
    ciphertexts = [item for row in plan.rows for item in row.fields if item is not None]
    rewritten = sum(any(item is not None and item.changed for item in row.fields) for row in plan.rows) if apply else 0
    return TableRotationResult(
        table=plan.spec.table,
        rows_scanned=len(plan.rows),
        ciphertexts_scanned=len(ciphertexts),
        ciphertexts_requiring_rotation=sum(item.requires_rotation for item in ciphertexts),
        rows_rewritten=rewritten,
    )


def _digest(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


__all__ = ["PostgresRotationResult", "rotate_postgres_ciphertexts", "verify_postgres_ciphertexts_primary_only"]
