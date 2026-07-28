BEGIN;

ALTER TABLE "worker_install_keys"
    ADD COLUMN IF NOT EXISTS "allowed_source" VARCHAR(64) NULL;

COMMENT ON COLUMN "worker_install_keys"."allowed_source"
    IS 'Authoritative Worker install-key registration source restriction (IP/CIDR only)';

COMMIT;
