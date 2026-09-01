from scripts.init_db import PERFORMANCE_INDEXES


def test_fresh_schema_initializer_creates_registration_id_unique_index() -> None:
    source = dict(PERFORMANCE_INDEXES)["idx_worker_install_keys_registration_id_unique"]

    assert "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS" in source
    assert '"registration_id" IS NOT NULL' in source
