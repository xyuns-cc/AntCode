-- Align long-lived database types and remove redundant public_id B-trees.
-- DROP INDEX CONCURRENTLY must remain outside an explicit transaction.

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
END $$;

-- Only drop a plain single-column public_id B-tree when a valid/ready full
-- unique B-tree has exactly the same key operator class, collation and options.
SELECT format('DROP INDEX CONCURRENTLY IF EXISTS %I.%I;', index_schema, index_name)
  FROM (
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
  ) redundant_public_id_indexes
\gexec
