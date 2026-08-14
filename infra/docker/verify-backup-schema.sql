DO $verify_schema$
DECLARE
    missing_tables TEXT[];
    incompatible_columns TEXT[];
    missing_indexes TEXT[];
    invalid_indexes TEXT[];
    unvalidated_constraints TEXT[];
    plaintext_worker_columns TEXT[];
BEGIN
    SELECT array_agg(required_name ORDER BY required_name) INTO missing_tables
      FROM unnest(ARRAY[
        'users', 'workers', 'worker_heartbeats', 'worker_events',
        'worker_install_keys', 'worker_projects', 'worker_project_files',
        'scheduled_tasks', 'task_executions', 'task_run_lease_generations',
        'task_logs', 'projects', 'project_files', 'project_rules',
        'project_codes', 'project_sources',
        'runtimes', 'project_runtime_bindings', 'crawl_batches', 'audit_logs',
        'system_configs', 'git_credentials', 'git_repositories',
        'source_artifacts', 'source_artifact_chunks', 'run_source_snapshots',
        'worker_performance_history', 'spider_metrics_history',
        'user_worker_permissions', 'user_sessions', 'scheduler_outbox', 'scheduler_authority',
        'antcode_data_migrations'
      ]) AS required(required_name)
     WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = required_name
           AND table_type = 'BASE TABLE'
     );
    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION 'required restored tables are missing: %', missing_tables;
    END IF;

    SELECT array_agg(
        format('%s.%s', expected.table_name, expected.column_name)
        ORDER BY expected.table_name, expected.column_name
    ) INTO incompatible_columns
      FROM (VALUES
        ('workers', 'api_key_hash', 'varchar', 'YES'),
        ('workers', 'secret_key_hash', 'varchar', 'YES'),
        ('workers', 'secret_key_encrypted', 'text', 'YES'),
        ('task_executions', 'lease_id', 'varchar', 'YES'),
        ('task_executions', 'lease_gen', 'int8', 'YES'),
        ('task_executions', 'cancel_requested_at', 'timestamptz', 'YES'),
        ('task_executions', 'scheduler_fencing_token', 'int8', 'YES'),
        ('scheduler_outbox', 'consume_attempts', 'int4', 'NO'),
        ('scheduled_tasks', 'project_id', 'int8', 'NO'),
        ('project_rules', 'region', 'varchar', 'YES'),
        ('project_rules', 'require_render', 'bool', 'NO'),
        ('task_run_lease_generations', 'id', 'int8', 'NO'),
        ('task_run_lease_generations', 'run_id', 'varchar', 'NO'),
        ('task_run_lease_generations', 'worker_id', 'int8', 'NO'),
        ('task_run_lease_generations', 'lease_id', 'varchar', 'NO')
      ) AS expected(table_name, column_name, udt_name, is_nullable)
      LEFT JOIN information_schema.columns actual
        ON actual.table_schema = 'public'
       AND actual.table_name = expected.table_name
       AND actual.column_name = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.udt_name IS DISTINCT FROM expected.udt_name
        OR actual.is_nullable IS DISTINCT FROM expected.is_nullable;
    IF incompatible_columns IS NOT NULL THEN
        RAISE EXCEPTION 'required restored columns are missing/incompatible: %', incompatible_columns;
    END IF;

    SELECT array_agg(required_name ORDER BY required_name) INTO missing_indexes
      FROM unnest(ARRAY[
        'idx_workers_api_key_hash', 'idx_task_executions_lease_gen',
        'idx_task_executions_cancel_requested_at',
        'idx_task_executions_scheduler_fencing_token',
        'idx_task_logs_event_id_unique', 'idx_task_logs_run_id_id',
        'idx_worker_install_keys_registration_id_unique',
        'idx_project_rules_region', 'idx_scheduled_tasks_project_id'
      ]) AS required(required_name)
     WHERE to_regclass(format('public.%I', required_name)) IS NULL;
    IF missing_indexes IS NOT NULL THEN
        RAISE EXCEPTION 'required restored indexes are missing: %', missing_indexes;
    END IF;

    SELECT array_agg(index_class.relname ORDER BY index_class.relname) INTO invalid_indexes
      FROM pg_index index_row
      JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
      JOIN pg_class table_class ON table_class.oid = index_row.indrelid
      JOIN pg_namespace namespace ON namespace.oid = table_class.relnamespace
     WHERE namespace.nspname = 'public'
       AND (NOT index_row.indisvalid OR NOT index_row.indisready);
    IF invalid_indexes IS NOT NULL THEN
        RAISE EXCEPTION 'restored public indexes are invalid/not ready: %', invalid_indexes;
    END IF;

    SELECT array_agg(constraint_row.conname ORDER BY constraint_row.conname)
      INTO unvalidated_constraints
      FROM pg_constraint constraint_row
      JOIN pg_namespace namespace ON namespace.oid = constraint_row.connamespace
     WHERE namespace.nspname = 'public' AND NOT constraint_row.convalidated;
    IF unvalidated_constraints IS NOT NULL THEN
        RAISE EXCEPTION 'restored public constraints are not validated: %', unvalidated_constraints;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint constraint_row
         WHERE constraint_row.conname = 'fk_scheduled_tasks_project_id'
           AND constraint_row.conrelid = 'public.scheduled_tasks'::regclass
           AND constraint_row.confrelid = 'public.projects'::regclass
           AND constraint_row.confdeltype = 'r'
           AND constraint_row.convalidated
    ) THEN
        RAISE EXCEPTION 'required scheduled_tasks -> projects RESTRICT foreign key is missing/incompatible';
    END IF;

    SELECT array_agg(column_name ORDER BY column_name) INTO plaintext_worker_columns
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'workers'
       AND column_name IN ('api_key', 'secret_key', 'api_key_previous');
    IF plaintext_worker_columns IS NOT NULL THEN
        RAISE EXCEPTION 'legacy plaintext Worker credential columns remain: %', plaintext_worker_columns;
    END IF;
END
$verify_schema$;
