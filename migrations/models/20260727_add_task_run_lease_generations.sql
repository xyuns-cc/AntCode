BEGIN;

DO $$
DECLARE
    invalid_columns INTEGER;
    required_columns INTEGER;
    primary_key_columns TEXT[];
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

        SELECT array_agg(attribute.attname ORDER BY key_column.ordinality)
          INTO primary_key_columns
          FROM pg_constraint constraint_row
          CROSS JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY AS key_column(attnum, ordinality)
          JOIN pg_attribute attribute
            ON attribute.attrelid = constraint_row.conrelid
           AND attribute.attnum = key_column.attnum
         WHERE constraint_row.conrelid = 'public.task_run_lease_generations'::regclass
           AND constraint_row.contype = 'p';
        IF primary_key_columns IS DISTINCT FROM ARRAY['id']::TEXT[] THEN
            RAISE EXCEPTION 'task_run_lease_generations 主键必须严格为 (id)，拒绝迁移';
        END IF;

    END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.task_run_lease_generations (
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

DO $$
DECLARE
    owned_sequence TEXT;
    identity_generation TEXT;
    id_default TEXT;
    created_at_default TEXT;
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
    SELECT columns.column_default INTO created_at_default
      FROM information_schema.columns
     WHERE columns.table_schema = 'public'
       AND columns.table_name = 'task_run_lease_generations'
       AND columns.column_name = 'created_at';
    IF created_at_default IS DISTINCT FROM 'CURRENT_TIMESTAMP' THEN
        RAISE EXCEPTION 'task_run_lease_generations.created_at default 必须为 CURRENT_TIMESTAMP，拒绝迁移';
    END IF;
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
END $$;

COMMIT;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_task_run_lease_generation_lookup
    ON public.task_run_lease_generations (run_id, worker_id, lease_id);

DO $$
DECLARE
    unique_definition TEXT;
    index_compatible BOOLEAN;
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

    SELECT index_row.indisvalid
           AND index_row.indisready
           AND NOT index_row.indisunique
           AND index_row.indnkeyatts = 3
           AND index_row.indnatts = 3
           AND index_row.indexprs IS NULL
           AND index_row.indpred IS NULL
           AND access_method.amname = 'btree'
           AND ARRAY(
               SELECT attribute.attname
                 FROM unnest(index_row.indkey) WITH ORDINALITY AS key_column(attnum, position)
                 JOIN pg_attribute attribute
                   ON attribute.attrelid = index_row.indrelid
                  AND attribute.attnum = key_column.attnum
                ORDER BY key_column.position
           ) = ARRAY['run_id', 'worker_id', 'lease_id']::NAME[]
      INTO index_compatible
      FROM pg_index index_row
      JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
      JOIN pg_class table_class ON table_class.oid = index_row.indrelid
      JOIN pg_namespace namespace ON namespace.oid = table_class.relnamespace
      JOIN pg_am access_method ON access_method.oid = index_class.relam
     WHERE namespace.nspname = 'public'
       AND table_class.relname = 'task_run_lease_generations'
       AND index_class.relname = 'idx_task_run_lease_generation_lookup';
    IF index_compatible IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'idx_task_run_lease_generation_lookup 定义不兼容，拒绝迁移';
    END IF;
END $$;
