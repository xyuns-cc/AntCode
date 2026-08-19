from pathlib import Path

from tests.integration.postgres.migration_cases import MIGRATION_CASES
from tests.integration.postgres.migration_support import MIGRATION_PATHS


def test_postgres_migrations_are_sql_only():
    migration_dir = Path("migrations/models")
    assert not list(migration_dir.glob("*.py"))
    assert list(migration_dir.glob("*.sql"))


def test_task_logs_upgrade_migration_matches_current_model():
    source = Path("migrations/models/20260710_add_task_logs.sql").read_text(encoding="utf-8")

    assert 'CREATE TABLE IF NOT EXISTS "task_logs"' in source
    assert '"run_id" VARCHAR(64) NOT NULL' in source
    assert "idx_task_logs_event_id_unique" in source


def test_migration_inventory_covers_every_sql_file() -> None:
    """比对纯静态，不碰数据库——所以它必须留在 release-gate 会跑的 unit 套件里。

    这条判据原先住在 tests/integration/postgres，而 integration 需要真 PostgreSQL、
    被 pyproject 的 testpaths 排除在 `make test` 之外，等于只在有人手动跑 make test-int
    时才生效。结果是新增 SQL 迁移却忘登记 MIGRATION_CASES 连续复发三次
    （91ec62f 补录一次、7b6fba0 补录 20260817/20260818、随后 20260819 再犯），
    每次都要等真机验证才暴露。搬到这里后，漏登记在提交前的常规门禁就会红。
    """
    discovered_names = tuple(path.name for path in MIGRATION_PATHS)
    case_names = tuple(case.name for case in MIGRATION_CASES)

    assert case_names == discovered_names
