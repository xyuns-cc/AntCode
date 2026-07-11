CREATE TABLE IF NOT EXISTS "scheduler_outbox" (
    "id" BIGSERIAL PRIMARY KEY,
    "public_id" VARCHAR(32) NOT NULL UNIQUE,
    "event_type" VARCHAR(64) NOT NULL,
    "aggregate_type" VARCHAR(32) NOT NULL,
    "aggregate_id" VARCHAR(64) NOT NULL,
    "payload" JSONB NOT NULL,
    "attempts" INTEGER NOT NULL DEFAULT 0,
    "available_at" TIMESTAMPTZ NOT NULL,
    "published_at" TIMESTAMPTZ NULL,
    "last_error" TEXT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS "idx_scheduler_outbox_pending"
    ON "scheduler_outbox" ("published_at", "available_at", "id");

CREATE INDEX IF NOT EXISTS "idx_scheduler_outbox_aggregate"
    ON "scheduler_outbox" ("aggregate_type", "aggregate_id");
