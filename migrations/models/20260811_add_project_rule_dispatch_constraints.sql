-- 持久化规则项目的区域与浏览器渲染调度约束。
-- 索引在事务外并发创建，避免升级时阻塞 project_rules 写入。

DO $$
DECLARE
    bad RECORD;
BEGIN
    FOR bad IN
        SELECT column_name, data_type, character_maximum_length, is_nullable, column_default
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'project_rules'
           AND column_name IN ('region', 'require_render')
           AND (
               (
                   column_name = 'region'
                   AND (
                       data_type <> 'character varying'
                       OR character_maximum_length <> 50
                       OR is_nullable <> 'YES'
                   )
               )
               OR (
                   column_name = 'require_render'
                   AND (
                       data_type <> 'boolean'
                       OR is_nullable <> 'NO'
                       OR column_default IS DISTINCT FROM 'false'
                   )
               )
           )
    LOOP
        RAISE EXCEPTION 'project_rules.% 已存在且定义不兼容，拒绝迁移', bad.column_name;
    END LOOP;
END $$;

ALTER TABLE public.project_rules
    ADD COLUMN IF NOT EXISTS region VARCHAR(50) NULL,
    ADD COLUMN IF NOT EXISTS require_render BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_project_rules_region
    ON public.project_rules (region);

DO $$
DECLARE
    compatible BOOLEAN;
BEGIN
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
        ON attribute.attrelid = table_class.oid AND attribute.attname = 'region'
     WHERE namespace.nspname = 'public'
       AND table_class.relname = 'project_rules'
       AND index_class.relname = 'idx_project_rules_region';
    IF compatible IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'idx_project_rules_region 缺失或定义不兼容，拒绝迁移';
    END IF;
END $$;
