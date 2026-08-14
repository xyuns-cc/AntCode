from scripts.init_db_schema_contracts import (
    ColumnContract,
    ForeignKeyContract,
    GeneratedPrimaryKeyContract,
    IndexContract,
    UniqueColumnsContract,
)
from scripts.init_db_schema_validation import (
    _column_errors,
    _foreign_key_errors,
    _generated_pk_errors,
    _index_errors,
    _unique_errors,
)

EXPECTED_COLUMN_ERROR_COUNT = 4
EXPECTED_INDEX_ERROR_COUNT = 3
EXPECTED_PRIMARY_KEY_ERROR_COUNT = 2


def test_column_contract_rejects_wrong_type_length_nullability_and_default() -> None:
    contract = ColumnContract(
        table="scheduler_outbox",
        name="consume_attempts",
        udt_name="int4",
        nullable=False,
        default="0",
    )
    wrong = {
        "udt_name": "int8",
        "character_maximum_length": 8,
        "is_nullable": "YES",
        "column_default": "1",
    }

    errors = _column_errors(contract, wrong)

    assert len(errors) == EXPECTED_COLUMN_ERROR_COUNT
    assert any("类型" in error for error in errors)
    assert any("长度" in error for error in errors)
    assert any("nullable" in error for error in errors)
    assert any("default" in error for error in errors)


def test_column_contract_accepts_postgres_casted_default() -> None:
    contract = ColumnContract(
        table="scheduler_outbox",
        name="consume_attempts",
        udt_name="int4",
        nullable=False,
        default="0",
    )
    actual = {
        "udt_name": "int4",
        "character_maximum_length": None,
        "is_nullable": "NO",
        "column_default": "0::integer",
    }

    assert _column_errors(contract, actual) == []


def test_index_contract_rejects_wrong_keys_unique_flag_and_predicate() -> None:
    contract = IndexContract(
        name="idx_registration",
        table="worker_install_keys",
        keys=("registration_id",),
        unique=True,
        predicate="registration_id IS NOT NULL",
    )
    wrong = {
        "table_name": "worker_install_keys",
        "access_method": "btree",
        "key_definitions": ["recovery_secret_hash"],
        "indisunique": False,
        "indisvalid": True,
        "indisready": True,
        "predicate": None,
    }

    errors = _index_errors(contract, wrong)

    assert len(errors) == EXPECTED_INDEX_ERROR_COUNT
    assert any("键应为" in error for error in errors)
    assert any("unique" in error for error in errors)
    assert any("谓词" in error for error in errors)


def test_index_contract_accepts_postgres_expression_casts() -> None:
    contract = IndexContract(
        name="idx_crawl",
        table="task_executions",
        keys=("result_data ->> 'crawl_batch_id'",),
        predicate="result_data ->> 'crawl_batch_id' IS NOT NULL",
    )
    actual = {
        "table_name": "task_executions",
        "access_method": "btree",
        "key_definitions": ["(result_data ->> 'crawl_batch_id'::text)"],
        "indisunique": False,
        "indisvalid": True,
        "indisready": True,
        "predicate": "((result_data ->> 'crawl_batch_id'::text) IS NOT NULL)",
    }

    assert _index_errors(contract, actual) == []


def test_index_contract_preserves_string_literal_case() -> None:
    contract = IndexContract(
        name="idx_crawl",
        table="task_executions",
        keys=("result_data ->> 'crawl_batch_id'",),
    )
    actual = {
        "table_name": "task_executions",
        "access_method": "btree",
        "key_definitions": ["(result_data ->> 'CRAWL_BATCH_ID'::text)"],
        "indisunique": False,
        "indisvalid": True,
        "indisready": True,
        "predicate": None,
    }

    assert any("键应为" in error for error in _index_errors(contract, actual))


def test_index_contract_rejects_non_btree_access_method() -> None:
    contract = IndexContract(name="idx_region", table="workers", keys=("region",))
    actual = {
        "table_name": "workers",
        "access_method": "hash",
        "key_definitions": ["region"],
        "indisunique": False,
        "indisvalid": True,
        "indisready": True,
        "predicate": None,
    }

    assert any("访问方法" in error for error in _index_errors(contract, actual))


def test_generated_primary_key_requires_exact_pk_and_sequence_default() -> None:
    contract = GeneratedPrimaryKeyContract(
        table="task_run_lease_generations",
        columns=("id",),
        generated_column="id",
    )
    columns = {
        ("task_run_lease_generations", "id"): {
            "column_default": None,
            "identity_generation": None,
            "owned_sequence_oid": None,
            "default_sequence_oid": None,
        }
    }
    constraints = [{"table_name": "task_run_lease_generations", "contype": "p", "columns": ["run_id"]}]

    errors = _generated_pk_errors(contract, columns, constraints)

    assert len(errors) == EXPECTED_PRIMARY_KEY_ERROR_COUNT
    assert any("主键" in error for error in errors)
    assert any("sequence/identity" in error for error in errors)


def test_generated_primary_key_accepts_exact_owned_sequence_default() -> None:
    contract = GeneratedPrimaryKeyContract(
        table="task_run_lease_generations",
        columns=("id",),
        generated_column="id",
    )
    sequence_name = "task_run_lease_generations_id_seq"
    columns = {
        ("task_run_lease_generations", "id"): {
            "column_default": f"nextval('{sequence_name}'::regclass)",
            "identity_generation": None,
            "owned_sequence_oid": 101,
            "default_sequence_oid": 101,
        }
    }
    constraints = [{"table_name": "task_run_lease_generations", "contype": "p", "columns": ["id"]}]

    assert _generated_pk_errors(contract, columns, constraints) == []


def test_generated_primary_key_rejects_expression_around_nextval() -> None:
    contract = GeneratedPrimaryKeyContract(
        table="task_run_lease_generations",
        columns=("id",),
        generated_column="id",
    )
    sequence_name = "task_run_lease_generations_id_seq"
    columns = {
        ("task_run_lease_generations", "id"): {
            "column_default": f"nextval('{sequence_name}'::regclass) + 0",
            "identity_generation": None,
            "owned_sequence_oid": 101,
            "default_sequence_oid": 101,
        }
    }
    constraints = [{"table_name": "task_run_lease_generations", "contype": "p", "columns": ["id"]}]

    assert any("未精确引用" in error for error in _generated_pk_errors(contract, columns, constraints))


def test_unique_columns_contract_requires_exact_ordered_columns() -> None:
    contract = UniqueColumnsContract(
        table="task_run_lease_generations",
        columns=("run_id", "lease_id"),
    )
    constraints = [
        {
            "table_name": "task_run_lease_generations",
            "contype": "u",
            "columns": ["lease_id", "run_id"],
        }
    ]

    assert _unique_errors(contract, constraints) == ["task_run_lease_generations 缺少唯一约束 ('run_id', 'lease_id')"]


def test_foreign_key_contract_requires_valid_restrict_constraint() -> None:
    contract = ForeignKeyContract(
        name="fk_scheduled_tasks_project_id",
        table="scheduled_tasks",
        columns=("project_id",),
        referenced_table="projects",
        referenced_columns=("id",),
        delete_action="r",
    )
    invalid = {
        "conname": "fk_scheduled_tasks_project_id",
        "contype": "f",
        "table_name": "scheduled_tasks",
        "columns": ["project_id"],
        "referenced_table": "projects",
        "referenced_schema": "other_schema",
        "referenced_columns": ["id"],
        "confdeltype": "c",
        "convalidated": False,
    }

    errors = _foreign_key_errors(contract, [invalid])

    assert len(errors) == 1
    assert "定义不兼容" in errors[0]
