BEGIN;

DO $$
DECLARE
    invalid_columns INTEGER;
    required_columns INTEGER;
BEGIN
    IF to_regclass('public.task_run_lease_generations') IS NOT NULL THEN
        SELECT COUNT(*) INTO required_columns
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'task_run_lease_generations'
           AND column_name IN (
               'id', 'run_id', 'worker_id', 'lease_id', 'lease_gen',
               'log_valid_through_id', 'created_at', 'closed_at'
           );
        SELECT COUNT(*) INTO invalid_columns
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'task_run_lease_generations'
           AND (
               (column_name = 'id' AND data_type <> 'bigint')
               OR (column_name = 'run_id' AND (data_type <> 'character varying' OR character_maximum_length <> 64))
               OR (column_name = 'worker_id' AND data_type <> 'bigint')
               OR (column_name = 'lease_id' AND (data_type <> 'character varying' OR character_maximum_length <> 64))
               OR (column_name = 'lease_gen' AND data_type <> 'bigint')
               OR (
                   column_name = 'log_valid_through_id'
                   AND (data_type <> 'character varying' OR character_maximum_length <> 41)
               )
               OR (column_name IN ('created_at', 'closed_at') AND data_type <> 'timestamp with time zone')
               OR (column_name IN ('id', 'run_id', 'worker_id', 'lease_id', 'created_at') AND is_nullable <> 'NO')
               OR (column_name IN ('lease_gen', 'log_valid_through_id', 'closed_at') AND is_nullable <> 'YES')
           );
        IF required_columns <> 8 OR invalid_columns > 0 THEN
            RAISE EXCEPTION 'task_run_lease_generations 已存在不兼容列，拒绝迁移';
        END IF;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS task_run_lease_generations (
    id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL,
    worker_id BIGINT NOT NULL,
    lease_id VARCHAR(64) NOT NULL,
    lease_gen BIGINT NULL,
    log_valid_through_id VARCHAR(41) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMPTZ NULL,
    CONSTRAINT uq_task_run_lease_generation UNIQUE (run_id, lease_id)
);

CREATE INDEX IF NOT EXISTS idx_task_run_lease_generation_lookup
    ON task_run_lease_generations (run_id, worker_id, lease_id);

DO $$
DECLARE
    unique_definition TEXT;
    index_definition TEXT;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO unique_definition
      FROM pg_constraint
     WHERE conrelid = 'public.task_run_lease_generations'::regclass
       AND contype = 'u'
       AND pg_get_constraintdef(oid) = 'UNIQUE (run_id, lease_id)'
     LIMIT 1;
    IF unique_definition IS DISTINCT FROM 'UNIQUE (run_id, lease_id)' THEN
        RAISE EXCEPTION 'uq_task_run_lease_generation 缺失或定义不兼容，拒绝迁移';
    END IF;

    SELECT pg_get_indexdef(indexrelid) INTO index_definition
      FROM pg_index
     WHERE indexrelid = 'public.idx_task_run_lease_generation_lookup'::regclass
       AND indisvalid;
    IF index_definition IS NULL OR index_definition NOT LIKE '% USING btree (run_id, worker_id, lease_id)' THEN
        RAISE EXCEPTION 'idx_task_run_lease_generation_lookup 定义不兼容，拒绝迁移';
    END IF;
END $$;

COMMIT;
