BEGIN;

CREATE TABLE IF NOT EXISTS "user_sessions" (
    "id" SERIAL PRIMARY KEY,
    "user_id" BIGINT NOT NULL,
    "jti" VARCHAR(64) NOT NULL UNIQUE,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "expires_at" TIMESTAMPTZ NOT NULL,
    "revoked_at" TIMESTAMPTZ NULL,
    "device_info" VARCHAR(256) NULL
);

CREATE INDEX IF NOT EXISTS "idx_user_sessions_user_id"
    ON "user_sessions" ("user_id");
CREATE INDEX IF NOT EXISTS "idx_user_sessions_expires_at"
    ON "user_sessions" ("expires_at");
CREATE INDEX IF NOT EXISTS "idx_user_sessions_user_revoked"
    ON "user_sessions" ("user_id", "revoked_at");

COMMIT;
