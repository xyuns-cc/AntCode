"""PostgreSQL catalog validation for the production database init path."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from scripts.init_db_schema_contracts import (
    COLUMN_CONTRACTS,
    FOREIGN_KEYS,
    GENERATED_PRIMARY_KEYS,
    INDEX_CONTRACTS,
    UNIQUE_COLUMNS,
    ColumnContract,
    ForeignKeyContract,
    GeneratedPrimaryKeyContract,
    IndexContract,
    UniqueColumnsContract,
)

_COLUMN_CATALOG_SQL = """
SELECT table_name, column_name, udt_name, character_maximum_length,
       is_nullable, column_default, identity_generation,
       to_regclass(pg_get_serial_sequence(
           format('%I.%I', column_row.table_schema, column_row.table_name),
           column_row.column_name
       ))::OID AS owned_sequence_oid,
       (
           SELECT dependency.refobjid
             FROM pg_class table_class
             JOIN pg_namespace namespace ON namespace.oid = table_class.relnamespace
             JOIN pg_attrdef default_row
               ON default_row.adrelid = table_class.oid
              AND default_row.adnum = column_row.ordinal_position
             JOIN pg_depend dependency
               ON dependency.classid = 'pg_attrdef'::regclass
              AND dependency.objid = default_row.oid
              AND dependency.refclassid = 'pg_class'::regclass
             JOIN pg_class sequence_class
               ON sequence_class.oid = dependency.refobjid
              AND sequence_class.relkind = 'S'
            WHERE namespace.nspname = column_row.table_schema
              AND table_class.relname = column_row.table_name
            LIMIT 1
       ) AS default_sequence_oid
  FROM information_schema.columns column_row
 WHERE table_schema = 'public'
"""

_INDEX_CATALOG_SQL = """
SELECT index_class.relname AS index_name,
       table_class.relname AS table_name,
       access_method.amname AS access_method,
       index_row.indisunique,
       index_row.indisvalid,
       index_row.indisready,
       pg_get_expr(index_row.indpred, index_row.indrelid) AS predicate,
       -- pg_get_indexdef(oid, N, FALSE) 只返回该列的键表达式，**不含排序方向**；
       -- DESC 编码在 pg_index.indoption 的 bit 0（int2vector，0 基下标）。不拼回
       -- 方向的话，契约里写 'created_at DESC' 永远匹配不上，而真正的隐患是反过来：
       -- 索引被建成升序也照样"通过"，倒序扫描的查询计划悄悄退化。
       ARRAY(
           SELECT pg_get_indexdef(index_row.indexrelid, key_number, FALSE)
                  || CASE WHEN (index_row.indoption[key_number - 1] & 1) = 1 THEN ' DESC' ELSE '' END
             FROM generate_series(1, index_row.indnkeyatts) AS key_number
            ORDER BY key_number
       ) AS key_definitions
  FROM pg_index index_row
  JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
  JOIN pg_class table_class ON table_class.oid = index_row.indrelid
  JOIN pg_namespace namespace ON namespace.oid = table_class.relnamespace
  JOIN pg_am access_method ON access_method.oid = index_class.relam
 WHERE namespace.nspname = 'public'
"""

_CONSTRAINT_CATALOG_SQL = """
-- contype / confdeltype 是 PG 内部的 "char" 类型（单字节）。asyncpg 原样返回
-- bytes（b'p' / b'r'），与本模块用 str 做的比较恒不相等：主键与唯一约束永远
-- 检测不到、外键定义永远判为不兼容，整个 schema 校验不可能通过。显式转 text
-- 让校验与驱动的类型映射解耦。
SELECT table_class.relname AS table_name,
       constraint_row.conname,
       constraint_row.contype::text AS contype,
       referenced_class.relname AS referenced_table,
       referenced_namespace.nspname AS referenced_schema,
       constraint_row.confdeltype::text AS confdeltype,
       constraint_row.convalidated,
       ARRAY(
           SELECT attribute.attname
             FROM unnest(constraint_row.conkey) WITH ORDINALITY AS key_column(attnum, position)
             JOIN pg_attribute attribute
               ON attribute.attrelid = constraint_row.conrelid
              AND attribute.attnum = key_column.attnum
            ORDER BY key_column.position
       ) AS columns,
       ARRAY(
           SELECT attribute.attname
             FROM unnest(constraint_row.confkey) WITH ORDINALITY AS key_column(attnum, position)
             JOIN pg_attribute attribute
               ON attribute.attrelid = constraint_row.confrelid
              AND attribute.attnum = key_column.attnum
            ORDER BY key_column.position
       ) AS referenced_columns
  FROM pg_constraint constraint_row
  JOIN pg_class table_class ON table_class.oid = constraint_row.conrelid
  LEFT JOIN pg_class referenced_class ON referenced_class.oid = constraint_row.confrelid
  LEFT JOIN pg_namespace referenced_namespace ON referenced_namespace.oid = referenced_class.relnamespace
  JOIN pg_namespace namespace ON namespace.oid = table_class.relnamespace
 WHERE namespace.nspname = 'public'
   AND constraint_row.contype IN ('p', 'u', 'f')
"""


def _normalize_sql(value: object | None) -> str | None:
    if value is None:
        return None
    parts = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", str(value))
    return "".join(part if index % 2 else _normalize_unquoted_sql(part) for index, part in enumerate(parts))


def _normalize_unquoted_sql(value: str) -> str:
    without_text_casts = re.sub(r"::\s*(?:text|character\s+varying)\b", "", value, flags=re.IGNORECASE)
    return re.sub(r"[\s()]", "", without_text_casts.lower())


def _normalize_default(value: object | None) -> str | None:
    if value is None:
        return None
    return _normalize_sql(str(value).split("::", maxsplit=1)[0].strip("'"))


def _column_errors(contract: ColumnContract, row: dict[str, Any] | None) -> list[str]:
    label = f"{contract.table}.{contract.name}"
    if row is None:
        return [f"缺少列 {label}"]
    errors: list[str] = []
    if row["udt_name"] != contract.udt_name:
        errors.append(f"{label} 类型应为 {contract.udt_name}，实际 {row['udt_name']}")
    if row["character_maximum_length"] != contract.max_length:
        errors.append(f"{label} 长度应为 {contract.max_length}，实际 {row['character_maximum_length']}")
    actual_nullable = row["is_nullable"] == "YES"
    if actual_nullable != contract.nullable:
        errors.append(f"{label} nullable 应为 {contract.nullable}，实际 {actual_nullable}")
    if contract.check_default and _normalize_default(row["column_default"]) != contract.default:
        errors.append(f"{label} default 应为 {contract.default!r}，实际 {row['column_default']!r}")
    return errors


def _index_errors(contract: IndexContract, row: dict[str, Any] | None) -> list[str]:
    if row is None:
        return [f"缺少索引 {contract.name}"]
    errors: list[str] = []
    actual_keys = tuple(_normalize_sql(key) for key in row["key_definitions"])
    expected_keys = tuple(_normalize_sql(key) for key in contract.keys)
    if row["table_name"] != contract.table:
        errors.append(f"{contract.name} 所属表应为 {contract.table}，实际 {row['table_name']}")
    if actual_keys != expected_keys:
        errors.append(f"{contract.name} 键应为 {contract.keys}，实际 {row['key_definitions']}")
    if bool(row["indisunique"]) != contract.unique:
        errors.append(f"{contract.name} unique 应为 {contract.unique}")
    if row["access_method"] != contract.access_method:
        errors.append(f"{contract.name} 访问方法应为 {contract.access_method}，实际 {row['access_method']}")
    if not row["indisvalid"] or not row["indisready"]:
        errors.append(f"{contract.name} 不是 valid/ready")
    if _normalize_sql(row["predicate"]) != _normalize_sql(contract.predicate):
        errors.append(f"{contract.name} 谓词应为 {contract.predicate!r}，实际 {row['predicate']!r}")
    return errors


def _generated_pk_errors(
    contract: GeneratedPrimaryKeyContract,
    columns: dict[tuple[str, str], dict[str, Any]],
    constraints: list[dict[str, Any]],
) -> list[str]:
    primary_keys = [row for row in constraints if row["table_name"] == contract.table and row["contype"] == "p"]
    actual = tuple(primary_keys[0]["columns"]) if len(primary_keys) == 1 else None
    errors = [] if actual == contract.columns else [f"{contract.table} 主键应为 {contract.columns}，实际 {actual}"]
    generated = columns.get((contract.table, contract.generated_column))
    if generated is None:
        return [*errors, f"缺少生成主键列 {contract.table}.{contract.generated_column}"]
    owned_sequence_oid = generated.get("owned_sequence_oid")
    if owned_sequence_oid is None:
        errors.append(f"{contract.table}.{contract.generated_column} 缺少 owned sequence/identity")
        return errors
    if generated["identity_generation"] is not None:
        return errors
    default = str(generated["column_default"] or "")
    default_sequence_oid = generated.get("default_sequence_oid")
    exact_nextval = re.fullmatch(r"nextval\('(?:[^']|'')+'::regclass\)", default)
    if exact_nextval is None or default_sequence_oid != owned_sequence_oid:
        errors.append(f"{contract.table}.{contract.generated_column} default 未精确引用 owned sequence")
    return errors


def _unique_errors(contract: UniqueColumnsContract, constraints: list[dict[str, Any]]) -> list[str]:
    matched = any(
        row["table_name"] == contract.table and row["contype"] == "u" and tuple(row["columns"]) == contract.columns
        for row in constraints
    )
    return [] if matched else [f"{contract.table} 缺少唯一约束 {contract.columns}"]


def _foreign_key_errors(contract: ForeignKeyContract, constraints: list[dict[str, Any]]) -> list[str]:
    row = next(
        (item for item in constraints if item["conname"] == contract.name and item["table_name"] == contract.table),
        None,
    )
    if row is None:
        return [f"缺少外键 {contract.name}"]
    expected = (
        "f",
        contract.table,
        contract.columns,
        contract.referenced_schema,
        contract.referenced_table,
        contract.referenced_columns,
        contract.delete_action,
        True,
    )
    actual = (
        row["contype"],
        row["table_name"],
        tuple(row["columns"]),
        row["referenced_schema"],
        row["referenced_table"],
        tuple(row["referenced_columns"]),
        row["confdeltype"],
        bool(row["convalidated"]),
    )
    return [] if actual == expected else [f"{contract.name} 定义不兼容: {actual!r}"]


def _raise_schema_errors(error_groups: Iterable[list[str]]) -> None:
    errors = [error for group in error_groups for error in group]
    if errors:
        raise RuntimeError("数据库 schema 与运行时合同不一致:\n- " + "\n- ".join(errors))


async def validate_current_schema(connection: Any) -> None:
    """Validate types, defaults, uniqueness, predicates and generated PKs."""
    column_rows = await connection.execute_query_dict(_COLUMN_CATALOG_SQL)
    index_rows = await connection.execute_query_dict(_INDEX_CATALOG_SQL)
    constraints = await connection.execute_query_dict(_CONSTRAINT_CATALOG_SQL)
    columns = {(row["table_name"], row["column_name"]): row for row in column_rows}
    indexes = {row["index_name"]: row for row in index_rows}
    groups: list[list[str]] = []
    groups.extend(
        _column_errors(contract, columns.get((contract.table, contract.name))) for contract in COLUMN_CONTRACTS
    )
    groups.extend(_index_errors(contract, indexes.get(contract.name)) for contract in INDEX_CONTRACTS)
    groups.extend(_generated_pk_errors(contract, columns, constraints) for contract in GENERATED_PRIMARY_KEYS)
    groups.extend(_unique_errors(contract, constraints) for contract in UNIQUE_COLUMNS)
    groups.extend(_foreign_key_errors(contract, constraints) for contract in FOREIGN_KEYS)
    _raise_schema_errors(groups)


__all__ = ["validate_current_schema"]
