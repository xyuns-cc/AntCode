BEGIN;

ALTER TABLE "worker_install_keys"
    ADD COLUMN IF NOT EXISTS "registration_id" VARCHAR(32) NULL,
    ADD COLUMN IF NOT EXISTS "recovery_secret_hash" VARCHAR(64) NULL,
    ADD COLUMN IF NOT EXISTS "registration_request_hash" VARCHAR(64) NULL,
    ADD COLUMN IF NOT EXISTS "credential_derivation_version" SMALLINT NULL,
    ADD COLUMN IF NOT EXISTS "recovery_expires_at" TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS "registration_acknowledged_at" TIMESTAMPTZ NULL;

-- 注意：model 里 registration_id 声明为 unique=True（全量唯一索引，Tortoise
-- 自动命名）。此处升级路径改用**部分唯一索引**（仅约束非空值）。两者对
-- 非空 registration_id 的唯一性语义完全一致（PG 视多个 NULL 为互异），仅索引
-- 体积/命名不同；全新库由 generate_schemas 建 model 的全量唯一索引，既有集群
-- 走本 SQL 建部分唯一索引。这是刻意保留的等价分歧，不需要统一。
CREATE UNIQUE INDEX IF NOT EXISTS "idx_worker_install_keys_registration_id_unique"
    ON "worker_install_keys" ("registration_id")
    WHERE "registration_id" IS NOT NULL;

CREATE INDEX IF NOT EXISTS "idx_worker_install_keys_unacknowledged_recovery"
    ON "worker_install_keys" ("recovery_expires_at")
    WHERE "status" = 'used' AND "registration_acknowledged_at" IS NULL;

COMMIT;
