-- A repository subdirectory may back multiple independently configured projects.
-- project_id remains the one-to-one project source binding.

DO $$
DECLARE
    constraint_row RECORD;
BEGIN
    FOR constraint_row IN
        SELECT constraint_name.conname
          FROM pg_constraint constraint_name
          JOIN pg_class table_class ON table_class.oid = constraint_name.conrelid
          JOIN pg_namespace namespace ON namespace.oid = table_class.relnamespace
         WHERE namespace.nspname = 'public'
           AND table_class.relname = 'project_sources'
           AND constraint_name.contype = 'u'
           AND constraint_name.conkey = ARRAY[
               (SELECT attnum FROM pg_attribute
                 WHERE attrelid = table_class.oid AND attname = 'repository_id'),
               (SELECT attnum FROM pg_attribute
                 WHERE attrelid = table_class.oid AND attname = 'subdir')
           ]::SMALLINT[]
    LOOP
        EXECUTE format(
            'ALTER TABLE public.project_sources DROP CONSTRAINT %I',
            constraint_row.conname
        );
    END LOOP;
END $$;

SELECT format('DROP INDEX CONCURRENTLY IF EXISTS %I.%I;', namespace.nspname, index_class.relname)
  FROM pg_index index_row
  JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
  JOIN pg_namespace namespace ON namespace.oid = index_class.relnamespace
  JOIN pg_am access_method ON access_method.oid = index_class.relam
 WHERE index_row.indrelid = 'public.project_sources'::regclass
   AND index_row.indisunique
   AND index_row.indisvalid
   AND index_row.indisready
   AND access_method.amname = 'btree'
   AND index_row.indnkeyatts = 2
   AND index_row.indnatts = 2
   AND index_row.indexprs IS NULL
   AND index_row.indpred IS NULL
   -- indkey 是 int2vector：既没有 `int2vector = smallint[]` 操作符（直接比较报
   -- UndefinedFunction），朴素的 indkey::int2[] 又是 0 基下标（[0:1]），与
   -- 1 基的 ARRAY[...] 恒不相等——那会静默漏删旧唯一索引。用 string_to_array
   -- 归一成 1 基 smallint[] 再比。
   AND string_to_array(index_row.indkey::text, ' ')::SMALLINT[] = ARRAY[
       (SELECT attnum FROM pg_attribute
         WHERE attrelid = index_row.indrelid AND attname = 'repository_id'),
       (SELECT attnum FROM pg_attribute
         WHERE attrelid = index_row.indrelid AND attname = 'subdir')
   ]::SMALLINT[]
   AND NOT EXISTS (
       SELECT 1 FROM pg_constraint constraint_row
        WHERE constraint_row.conindid = index_row.indexrelid
   )
\gexec

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_project_sources_repository_subdir
    ON public.project_sources (repository_id, subdir);

DO $$
DECLARE
    compatible BOOLEAN;
BEGIN
    SELECT index_row.indisvalid
           AND index_row.indisready
           AND NOT index_row.indisunique
           AND index_row.indnkeyatts = 2
           AND index_row.indnatts = 2
           AND index_row.indexprs IS NULL
           AND index_row.indpred IS NULL
           AND access_method.amname = 'btree'
           AND index_row.indkey[0] = repository_attribute.attnum
           AND index_row.indkey[1] = subdir_attribute.attnum
      INTO compatible
      FROM pg_index index_row
      JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
      JOIN pg_class table_class ON table_class.oid = index_row.indrelid
      JOIN pg_namespace namespace ON namespace.oid = table_class.relnamespace
      JOIN pg_am access_method ON access_method.oid = index_class.relam
      JOIN pg_attribute repository_attribute
        ON repository_attribute.attrelid = table_class.oid
       AND repository_attribute.attname = 'repository_id'
      JOIN pg_attribute subdir_attribute
        ON subdir_attribute.attrelid = table_class.oid
       AND subdir_attribute.attname = 'subdir'
     WHERE namespace.nspname = 'public'
       AND table_class.relname = 'project_sources'
       AND index_class.relname = 'idx_project_sources_repository_subdir';
    IF compatible IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'idx_project_sources_repository_subdir 缺失或定义不兼容，拒绝迁移';
    END IF;
END $$;
