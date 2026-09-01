from antcode_core.domain.models.task_log import TaskLog

from scripts.init_db import PERFORMANCE_INDEXES


def test_task_logs_storage_order_index_is_online_and_consistent():
    index = next(item for item in TaskLog._meta.indexes if getattr(item, "name", None) == "idx_task_logs_run_id_id")
    init_sql = dict(PERFORMANCE_INDEXES)["idx_task_logs_run_id_id"]

    assert index.fields == ["run_id", "id"]
    assert 'CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_task_logs_run_id_id"' in init_sql
    assert 'ON public."task_logs" ("run_id", "id")' in init_sql
