BEGIN;

ALTER TABLE "worker_install_keys"
    ADD COLUMN IF NOT EXISTS "registration_id" VARCHAR(32) NULL,
    ADD COLUMN IF NOT EXISTS "recovery_secret_hash" VARCHAR(64) NULL,
    ADD COLUMN IF NOT EXISTS "registration_request_hash" VARCHAR(64) NULL,
    ADD COLUMN IF NOT EXISTS "credential_derivation_version" SMALLINT NULL,
    ADD COLUMN IF NOT EXISTS "recovery_expires_at" TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS "registration_acknowledged_at" TIMESTAMPTZ NULL;

-- registration_id 的唯一性统一由规范命名的部分唯一索引负责；model 不再
-- 声明 unique=True，避免全新库额外生成随机命名的冗余全量唯一索引。
CREATE UNIQUE INDEX IF NOT EXISTS "idx_worker_install_keys_registration_id_unique"
    ON "worker_install_keys" ("registration_id")
    WHERE "registration_id" IS NOT NULL;

CREATE INDEX IF NOT EXISTS "idx_worker_install_keys_unacknowledged_recovery"
    ON "worker_install_keys" ("recovery_expires_at")
    WHERE "status" = 'used' AND "registration_acknowledged_at" IS NULL;

COMMIT;
