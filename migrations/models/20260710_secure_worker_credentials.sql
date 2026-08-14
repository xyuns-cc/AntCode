BEGIN;

ALTER TABLE public."workers"
    ADD COLUMN IF NOT EXISTS "api_key_hash" VARCHAR(128) NULL,
    ADD COLUMN IF NOT EXISTS "secret_key_hash" VARCHAR(128) NULL,
    ADD COLUMN IF NOT EXISTS "secret_key_encrypted" TEXT NULL,
    ADD COLUMN IF NOT EXISTS "api_key_previous_hash" VARCHAR(128) NULL,
    ADD COLUMN IF NOT EXISTS "api_key_previous_expires_at" TIMESTAMPTZ NULL;

COMMIT;

CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_workers_api_key_hash"
    ON public."workers" ("api_key_hash");
CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_workers_api_key_previous_hash"
    ON public."workers" ("api_key_previous_hash");

DO $$
DECLARE
    expected RECORD;
    compatible BOOLEAN;
BEGIN
    FOR expected IN
        SELECT * FROM (VALUES
            ('idx_workers_api_key_hash', 'api_key_hash'),
            ('idx_workers_api_key_previous_hash', 'api_key_previous_hash')
        ) AS indexes(index_name, column_name)
    LOOP
        SELECT index_row.indisvalid
               AND index_row.indisready
               AND NOT index_row.indisunique
               AND index_row.indnkeyatts = 1
               AND index_row.indnatts = 1
               AND index_row.indexprs IS NULL
               AND index_row.indpred IS NULL
               AND access_method.amname = 'btree'
               AND index_row.indkey[0] = attribute.attnum
          INTO compatible
          FROM pg_index index_row
          JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
          JOIN pg_class table_class ON table_class.oid = index_row.indrelid
          JOIN pg_namespace namespace ON namespace.oid = table_class.relnamespace
          JOIN pg_am access_method ON access_method.oid = index_class.relam
          JOIN pg_attribute attribute
            ON attribute.attrelid = table_class.oid
           AND attribute.attname = expected.column_name
         WHERE namespace.nspname = 'public'
           AND table_class.relname = 'workers'
           AND index_class.relname = expected.index_name;
        IF compatible IS DISTINCT FROM TRUE THEN
            RAISE EXCEPTION 'Worker 鉴权索引 % 缺失或定义不兼容，拒绝迁移', expected.index_name;
        END IF;
    END LOOP;
END $$;

-- 执行本补丁后、启动新版本前运行：
--   uv run python scripts/migrate_worker_credentials.py
-- 该脚本会在单事务中加密/哈希历史凭据并删除三个明文列。
