"""Strict, idempotent schema repairs shared by the production init path."""

from __future__ import annotations

from typing import Any

AUDIT_USER_ID_BIGINT_SQL = """
DO $$
DECLARE
    current_type TEXT;
BEGIN
    SELECT columns.udt_name
      INTO current_type
      FROM information_schema.columns
     WHERE columns.table_schema = 'public'
       AND columns.table_name = 'audit_logs'
       AND columns.column_name = 'user_id';
    IF current_type = 'int4' THEN
        ALTER TABLE public.audit_logs ALTER COLUMN user_id TYPE BIGINT USING user_id::BIGINT;
    ELSIF current_type IS DISTINCT FROM 'int8' THEN
        RAISE EXCEPTION 'audit_logs.user_id 缺失或类型不兼容(%)，拒绝迁移', current_type;
    END IF;
END $$
"""

TASK_RUN_GENERATION_SEQUENCE_SQL = """
DO $$
DECLARE
    owned_sequence TEXT;
    identity_generation TEXT;
    id_default TEXT;
    default_sequence_oids OID[];
    sequence_increment BIGINT;
    sequence_cache BIGINT;
    sequence_cycles BOOLEAN;
    sequence_last_value BIGINT;
    sequence_is_called BOOLEAN;
    next_sequence_value BIGINT;
    maximum_id BIGINT;
BEGIN
    LOCK TABLE public.task_run_lease_generations IN SHARE ROW EXCLUSIVE MODE;
    SELECT columns.identity_generation,
           columns.column_default,
           pg_get_serial_sequence('public.task_run_lease_generations', 'id')
      INTO identity_generation, id_default, owned_sequence
      FROM information_schema.columns
     WHERE columns.table_schema = 'public'
       AND columns.table_name = 'task_run_lease_generations'
       AND columns.column_name = 'id';
    IF owned_sequence IS NULL THEN
        RAISE EXCEPTION 'task_run_lease_generations.id 缺少 owned sequence/identity，拒绝迁移';
    END IF;
    IF identity_generation IS NULL THEN
        SELECT array_agg(DISTINCT dependency.refobjid)
          INTO default_sequence_oids
          FROM pg_attrdef default_row
          JOIN pg_depend dependency
            ON dependency.classid = 'pg_attrdef'::regclass
           AND dependency.objid = default_row.oid
           AND dependency.refclassid = 'pg_class'::regclass
          JOIN pg_class sequence_class
            ON sequence_class.oid = dependency.refobjid
           AND sequence_class.relkind = 'S'
         WHERE default_row.adrelid = 'public.task_run_lease_generations'::regclass
           AND default_row.adnum = (
               SELECT attnum FROM pg_attribute
                WHERE attrelid = 'public.task_run_lease_generations'::regclass AND attname = 'id'
           );
        IF cardinality(default_sequence_oids) IS DISTINCT FROM 1
           OR default_sequence_oids[1] IS DISTINCT FROM to_regclass(owned_sequence) THEN
            RAISE EXCEPTION 'task_run_lease_generations.id default 未精确引用 owned sequence，拒绝迁移';
        END IF;
        IF id_default IS DISTINCT FROM format(
            'nextval(%L::regclass)', default_sequence_oids[1]::regclass::TEXT
        ) THEN
            RAISE EXCEPTION 'task_run_lease_generations.id default 表达式不兼容，拒绝迁移';
        END IF;
    END IF;
    SELECT sequence_row.seqincrement, sequence_row.seqcache, sequence_row.seqcycle
      INTO sequence_increment, sequence_cache, sequence_cycles
      FROM pg_sequence sequence_row
     WHERE sequence_row.seqrelid = to_regclass(owned_sequence);
    IF sequence_increment IS NULL OR sequence_increment <= 0 THEN
        RAISE EXCEPTION 'task_run_lease_generations.id sequence 必须正向递增，拒绝迁移';
    END IF;
    IF sequence_cache IS DISTINCT FROM 1 OR sequence_cycles IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'task_run_lease_generations.id sequence 必须 CACHE 1 且 NO CYCLE，拒绝迁移';
    END IF;
    EXECUTE format('SELECT last_value, is_called FROM %s', owned_sequence)
       INTO sequence_last_value, sequence_is_called;
    SELECT MAX(id) INTO maximum_id FROM public.task_run_lease_generations;
    next_sequence_value := sequence_last_value + CASE WHEN sequence_is_called THEN sequence_increment ELSE 0 END;
    IF maximum_id IS NOT NULL AND next_sequence_value <= maximum_id THEN
        RAISE EXCEPTION 'task_run_lease_generations.id sequence 落后于 MAX(id)，拒绝迁移';
    END IF;
END $$
"""

REDUNDANT_PUBLIC_ID_INDEXES_SQL = """
SELECT namespace.nspname AS index_schema, index_class.relname AS index_name
  FROM pg_index candidate
  JOIN pg_class table_class ON table_class.oid = candidate.indrelid
  JOIN pg_namespace table_namespace ON table_namespace.oid = table_class.relnamespace
  JOIN pg_class index_class ON index_class.oid = candidate.indexrelid
  JOIN pg_namespace namespace ON namespace.oid = index_class.relnamespace
  JOIN pg_am candidate_method ON candidate_method.oid = index_class.relam
 WHERE table_namespace.nspname = 'public'
   AND NOT candidate.indisunique
   AND candidate.indnkeyatts = 1
   AND candidate.indnatts = 1
   AND candidate.indexprs IS NULL
   AND candidate.indpred IS NULL
   AND index_class.relkind = 'i'
   AND NOT candidate.indisclustered
   AND candidate_method.amname = 'btree'
   AND (
        SELECT attribute.attname
          FROM pg_attribute attribute
         WHERE attribute.attrelid = candidate.indrelid
           AND attribute.attnum = candidate.indkey[0]
   ) = 'public_id'
   AND NOT EXISTS (
        SELECT 1 FROM pg_constraint constraint_row
         WHERE constraint_row.conindid = candidate.indexrelid
   )
   AND EXISTS (
        SELECT 1
          FROM pg_index keeper
          JOIN pg_class keeper_class ON keeper_class.oid = keeper.indexrelid
          JOIN pg_am keeper_method ON keeper_method.oid = keeper_class.relam
         WHERE keeper.indrelid = candidate.indrelid
           AND keeper.indexrelid <> candidate.indexrelid
           AND keeper.indisvalid
           AND keeper.indisready
           AND keeper.indisunique
           AND keeper.indnkeyatts = 1
           AND keeper.indnatts = 1
           AND keeper.indexprs IS NULL
           AND keeper.indpred IS NULL
           AND keeper_class.relkind = 'i'
           AND keeper_method.amname = candidate_method.amname
           AND keeper.indkey[0] = candidate.indkey[0]
           AND keeper.indclass[0] = candidate.indclass[0]
           AND keeper.indcollation[0] = candidate.indcollation[0]
           AND keeper.indoption[0] = candidate.indoption[0]
           AND EXISTS (
                SELECT 1 FROM pg_constraint keeper_constraint
                 WHERE keeper_constraint.conindid = keeper.indexrelid
                   AND keeper_constraint.contype = 'u'
           )
   )
"""

TASK_PROJECT_FOREIGN_KEY_SQL = """
DO $$
DECLARE
    compatible BOOLEAN;
    project_id_type TEXT;
    null_project_count BIGINT;
BEGIN
    SELECT columns.udt_name
      INTO project_id_type
      FROM information_schema.columns
     WHERE columns.table_schema = 'public'
       AND columns.table_name = 'scheduled_tasks'
       AND columns.column_name = 'project_id';
    IF project_id_type IS DISTINCT FROM 'int8' THEN
        RAISE EXCEPTION 'scheduled_tasks.project_id 缺失或类型不兼容(%)，拒绝迁移', project_id_type;
    END IF;
    SELECT COUNT(*) INTO null_project_count
      FROM public.scheduled_tasks
     WHERE project_id IS NULL;
    IF null_project_count > 0 THEN
        RAISE EXCEPTION 'scheduled_tasks.project_id 存在 % 条 NULL，拒绝迁移', null_project_count;
    END IF;
    ALTER TABLE public.scheduled_tasks ALTER COLUMN project_id SET NOT NULL;
    SELECT constraint_row.contype = 'f'
           AND constraint_row.conrelid = 'public.scheduled_tasks'::regclass
           AND constraint_row.confrelid = 'public.projects'::regclass
           AND constraint_row.conkey = ARRAY[
               (SELECT attnum FROM pg_attribute
                 WHERE attrelid = 'public.scheduled_tasks'::regclass AND attname = 'project_id')
           ]::SMALLINT[]
           AND constraint_row.confkey = ARRAY[
               (SELECT attnum FROM pg_attribute WHERE attrelid = 'public.projects'::regclass AND attname = 'id')
           ]::SMALLINT[]
           AND constraint_row.confdeltype = 'r'
      INTO compatible
     FROM pg_constraint constraint_row
     WHERE constraint_row.conname = 'fk_scheduled_tasks_project_id'
       AND constraint_row.connamespace = 'public'::regnamespace
       AND constraint_row.conrelid = 'public.scheduled_tasks'::regclass;
    IF compatible IS FALSE THEN
        RAISE EXCEPTION 'fk_scheduled_tasks_project_id 已存在但定义不兼容，拒绝迁移';
    END IF;
    IF compatible IS NULL THEN
        ALTER TABLE public.scheduled_tasks
            ADD CONSTRAINT fk_scheduled_tasks_project_id
            FOREIGN KEY (project_id) REFERENCES public.projects(id)
            ON DELETE RESTRICT NOT VALID;
    END IF;
END $$
"""

VALIDATE_TASK_PROJECT_FOREIGN_KEY_SQL = """
ALTER TABLE public.scheduled_tasks VALIDATE CONSTRAINT fk_scheduled_tasks_project_id
"""


def _quoted_identifier(value: str) -> str:
    if not value:
        raise ValueError("数据库标识符不能为空")
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


async def align_database_integrity(connection: Any) -> tuple[str, ...]:
    """Upgrade audit IDs and remove only proven-redundant public ID indexes."""
    await connection.execute_query(AUDIT_USER_ID_BIGINT_SQL)
    await connection.execute_query(TASK_RUN_GENERATION_SEQUENCE_SQL)
    await connection.execute_query(TASK_PROJECT_FOREIGN_KEY_SQL)
    await connection.execute_query(VALIDATE_TASK_PROJECT_FOREIGN_KEY_SQL)
    rows = await connection.execute_query_dict(REDUNDANT_PUBLIC_ID_INDEXES_SQL)
    removed: list[str] = []
    for row in rows:
        schema = str(row["index_schema"])
        name = str(row["index_name"])
        qualified = f"{_quoted_identifier(schema)}.{_quoted_identifier(name)}"
        await connection.execute_query(f"DROP INDEX CONCURRENTLY IF EXISTS {qualified}")
        removed.append(f"{schema}.{name}")
    return tuple(removed)


__all__ = [
    "AUDIT_USER_ID_BIGINT_SQL",
    "REDUNDANT_PUBLIC_ID_INDEXES_SQL",
    "TASK_RUN_GENERATION_SEQUENCE_SQL",
    "TASK_PROJECT_FOREIGN_KEY_SQL",
    "VALIDATE_TASK_PROJECT_FOREIGN_KEY_SQL",
    "align_database_integrity",
]
