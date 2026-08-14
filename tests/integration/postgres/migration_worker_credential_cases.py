"""20260710_secure_worker_credentials.sql 的可执行迁移用例。

该迁移刻意分成两段：加列在 BEGIN/COMMIT 内，建索引用 CONCURRENTLY 走事务外
（CONCURRENTLY 不能在事务里执行）。因此索引步骤失败时先提交的加列不会回滚——
这是可接受的：列全是 ADD COLUMN IF NOT EXISTS，留下无害且整个文件可重跑。
本用例断言的是「数据不丢 + 不留半成品索引」，而非与 CONCURRENTLY 互斥的
「schema 整体回滚」。
"""

from .migration_support import FailureExpectation, MigrationCase, SchemaExpectation

WORKER_CREDENTIALS = MigrationCase(
    name="20260710_secure_worker_credentials.sql",
    setup_sql="""
        CREATE TABLE workers (id BIGINT PRIMARY KEY, api_key TEXT, secret_key TEXT, marker TEXT NOT NULL);
        INSERT INTO workers VALUES (1, 'legacy-api', 'legacy-secret', 'preserved');
    """,
    seed_after_first_sql="",
    marker_query="SELECT marker FROM workers WHERE id = 1",
    marker_value="preserved",
    schema=SchemaExpectation(
        table="workers",
        columns=(
            "api_key_hash",
            "secret_key_hash",
            "secret_key_encrypted",
            "api_key_previous_hash",
            "api_key_previous_expires_at",
        ),
        indexes=("idx_workers_api_key_hash", "idx_workers_api_key_previous_hash"),
    ),
    failure=FailureExpectation(
        setup_sql="""
            CREATE TABLE workers (id BIGINT PRIMARY KEY, api_key_hash JSON, marker TEXT NOT NULL);
            INSERT INTO workers VALUES (1, '{}', 'preserved');
        """,
        marker_query="SELECT marker FROM workers WHERE id = 1",
        marker_value="preserved",
        present_columns=(
            "id",
            "api_key_hash",
            "marker",
            "secret_key_hash",
            "secret_key_encrypted",
            "api_key_previous_hash",
            "api_key_previous_expires_at",
        ),
        absent_columns=(),
        absent_indexes=("idx_workers_api_key_hash", "idx_workers_api_key_previous_hash"),
    ),
)

__all__ = ["WORKER_CREDENTIALS"]
