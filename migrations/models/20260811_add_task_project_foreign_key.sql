-- Enforce the Task -> Project ownership invariant in PostgreSQL itself.
-- NOT VALID keeps the initial lock short while immediately protecting new writes.

DO $$
DECLARE
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
END $$;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_scheduled_tasks_project_id
    ON public.scheduled_tasks (project_id);

DO $$
DECLARE
    index_compatible BOOLEAN;
BEGIN
    SELECT index_row.indisvalid
           AND index_row.indisready
           AND NOT index_row.indisunique
           AND index_row.indnkeyatts = 1
           AND index_row.indnatts = 1
           AND index_row.indexprs IS NULL
           AND index_row.indpred IS NULL
           AND access_method.amname = 'btree'
           AND attribute.attname = 'project_id'
      INTO index_compatible
      FROM pg_index index_row
      JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
      JOIN pg_namespace index_namespace ON index_namespace.oid = index_class.relnamespace
      JOIN pg_class table_class ON table_class.oid = index_row.indrelid
      JOIN pg_namespace table_namespace ON table_namespace.oid = table_class.relnamespace
      JOIN pg_am access_method ON access_method.oid = index_class.relam
      JOIN pg_attribute attribute
        ON attribute.attrelid = table_class.oid
       AND attribute.attnum = index_row.indkey[0]
     WHERE index_namespace.nspname = 'public'
       AND index_class.relname = 'idx_scheduled_tasks_project_id'
       AND table_namespace.nspname = 'public'
       AND table_class.relname = 'scheduled_tasks';
    IF index_compatible IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'idx_scheduled_tasks_project_id 定义不兼容，拒绝迁移';
    END IF;
END $$;

ALTER TABLE public.scheduled_tasks ALTER COLUMN project_id SET NOT NULL;

DO $$
DECLARE
    compatible BOOLEAN;
BEGIN
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
END $$;

ALTER TABLE public.scheduled_tasks VALIDATE CONSTRAINT fk_scheduled_tasks_project_id;
