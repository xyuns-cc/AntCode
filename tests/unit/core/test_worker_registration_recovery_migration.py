from pathlib import Path

from scripts.init_db import PERFORMANCE_INDEXES


def test_worker_registration_recovery_migration_matches_model() -> None:
    source = Path("migrations/models/20260717_add_worker_registration_recovery.sql").read_text(encoding="utf-8")

    for column in (
        "registration_id",
        "recovery_secret_hash",
        "registration_request_hash",
        "credential_derivation_version",
        "recovery_expires_at",
        "registration_acknowledged_at",
    ):
        assert f'"{column}"' in source
    assert "IF NOT EXISTS" in source
    assert "registration_id_unique" in source
    assert "unacknowledged_recovery" in source


def test_fresh_schema_initializer_creates_registration_id_unique_index() -> None:
    source = dict(PERFORMANCE_INDEXES)["idx_worker_install_keys_registration_id_unique"]

    assert "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS" in source
    assert '"registration_id" IS NOT NULL' in source
