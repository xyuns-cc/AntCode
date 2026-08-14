from pathlib import Path

RELATION_SERVICE = Path("packages/antcode_core/src/antcode_core/application/services/projects/relation_service.py")
CASCADE_DELETE = Path("packages/antcode_core/src/antcode_core/application/services/projects/project_cascade_delete.py")


def test_project_cascade_deletes_run_logs_before_executions() -> None:
    source = CASCADE_DELETE.read_text(encoding="utf-8")
    log_delete = "TaskLog.filter(run_id__in=list(run_batch)).using_db(conn).delete()"
    execution_delete = "TaskRun.filter(task_id__in=list(task_ids)).using_db(conn).delete()"

    assert log_delete in source
    assert execution_delete in source
    assert source.index(log_delete) < source.index(execution_delete)


def test_project_cascade_deletes_lease_history_before_executions() -> None:
    source = CASCADE_DELETE.read_text(encoding="utf-8")
    history_delete = "TaskRunLeaseGeneration.filter(run_id__in=list(run_batch)).using_db(conn).delete()"
    execution_delete = "TaskRun.filter(task_id__in=list(task_ids)).using_db(conn).delete()"

    assert history_delete in source
    assert source.index(history_delete) < source.index(execution_delete)


def test_obsolete_non_transactional_task_cascade_is_removed() -> None:
    source = RELATION_SERVICE.read_text(encoding="utf-8")

    assert "delete_task_cascade" not in source


def test_project_cascade_enqueues_spider_storage_cleanup_before_run_delete() -> None:
    source = CASCADE_DELETE.read_text(encoding="utf-8")
    cleanup_event = 'event_type="spider_storage_cleanup"'
    execution_delete = "TaskRun.filter(task_id__in=list(task_ids)).using_db(conn).delete()"

    assert cleanup_event in source
    assert source.index(cleanup_event) < source.index(execution_delete)


def test_project_cascade_publishes_task_changed_for_each_deleted_task() -> None:
    """P1-FN-07: 批量删 Task 必须逐个发布 task_changed outbox 事件（与删除同
    事务），Master scheduler_event_loop 收到后才会移除对应 APScheduler job。"""
    source = CASCADE_DELETE.read_text(encoding="utf-8")
    task_changed_event = 'event_type="task_changed"'

    assert task_changed_event in source
    # 与单任务删除路径一致：走 outbox（connection=conn 同事务）
    task_changed_block = source[source.index(task_changed_event) :]
    assert "connection=conn" in task_changed_block[:600]
    # 主流程顺序：删除任务/执行 → 发布 task_changed → 删除其余关联
    assert source.index("_delete_tasks_and_runs(") < source.index("_publish_task_changed_events(")


def test_project_cascade_purges_task_logs_with_run_commit_locks_after_commit() -> None:
    """P1-DB-03: 事务提交后必须按 run 级 advisory lock 清扫日志残留，
    与在途 append_entries 串行化。"""
    source = CASCADE_DELETE.read_text(encoding="utf-8")

    assert "purge_task_logs_for_runs(run_ids)" in source
    # 清扫发生在事务块之后（TaskRun 已不存在，后续 append 会被锁内校验拒绝）
    post_commit_call = "await _run_post_commit_cleanup("
    assert source.index("_delete_project_relations(") < source.index(post_commit_call)


def test_project_cascade_persists_crawl_cleanup_before_database_delete() -> None:
    source = CASCADE_DELETE.read_text(encoding="utf-8")
    cleanup_event = 'event_type="crawl_project_cleanup"'
    batch_delete = "CrawlBatch.filter(project_id=project_id).using_db(conn).delete()"

    assert cleanup_event in source
    assert source.index("_capture_crawl_batch_ids(") < source.index("_delete_project_relations(")
    assert source.index(cleanup_event) < source.index(batch_delete)
