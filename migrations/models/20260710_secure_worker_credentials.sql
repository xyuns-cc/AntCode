BEGIN;

ALTER TABLE "workers"
    ADD COLUMN IF NOT EXISTS "api_key_hash" VARCHAR(128) NULL,
    ADD COLUMN IF NOT EXISTS "secret_key_hash" VARCHAR(128) NULL,
    ADD COLUMN IF NOT EXISTS "secret_key_encrypted" TEXT NULL,
    ADD COLUMN IF NOT EXISTS "api_key_previous_hash" VARCHAR(128) NULL,
    ADD COLUMN IF NOT EXISTS "api_key_previous_expires_at" TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS "idx_workers_api_key_hash"
    ON "workers" ("api_key_hash");
CREATE INDEX IF NOT EXISTS "idx_workers_api_key_previous_hash"
    ON "workers" ("api_key_previous_hash");

COMMIT;

-- 执行本补丁后、启动新版本前运行：
--   uv run python scripts/migrate_worker_credentials.py
-- 该脚本会在单事务中加密/哈希历史凭据并删除三个明文列。
