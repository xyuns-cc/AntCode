"""P1-DB-03: 删除路径与在途日志提交的 run 级 advisory lock 串行化。"""

import pytest
from antcode_core.application.services.logs.postgres_log_service import (
    PostgresLogEntry,
    PostgresLogService,
    TaskRunGoneError,
    run_commit_lock_key,
)
from antcode_core.application.services.logs.task_log_run_guard import (
    TASK_LOG_PURGE_BATCH_SIZE,
    purge_task_logs_for_runs,
)

# 断言用命名常量（禁止魔法数字）
_TWO_RUNS = 2
_TWO_TRANSACTIONS = 2


class _TxRecorder:
    """记录事务内 SQL 顺序的 fake 连接（同时充当上下文管理器）。"""

    def __init__(self, existing_run_ids=()):
        self.events: list[tuple[str, list]] = []
        self.inserted_rows = None
        self._existing = set(existing_run_ids)

    async def execute_query(self, sql, params):
        if "pg_advisory_xact_lock" in sql:
            self.events.append(("lock", list(params)))
            return 1, []
        if 'SELECT "run_id"' in sql:
            self.events.append(("exists_check", list(params)))
            found = [{"run_id": run_id} for run_id in params if run_id in self._existing]
            return len(found), found
        if sql.startswith("DELETE"):
            self.events.append(("delete", list(params)))
            return len(params), []
        raise AssertionError(f"未预期的 SQL: {sql}")

    async def execute_many(self, _sql, rows):
        self.events.append(("insert", [row[1] for row in rows]))
        self.inserted_rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _entry(run_id: str) -> PostgresLogEntry:
    return PostgresLogEntry(run_id=run_id, log_type="stdout", content="line", sequence=1)


@pytest.mark.asyncio
async def test_append_entries_rejects_deleted_run(monkeypatch):
    """TaskRun 已删除时锁内 EXISTS 校验必须显式拒绝，绝不插入孤儿日志。"""
    service = PostgresLogService()
    tx = _TxRecorder(existing_run_ids=())
    monkeypatch.setattr("tortoise.transactions.in_transaction", lambda _name: tx)

    with pytest.raises(TaskRunGoneError, match="run-gone"):
        await service.append_entries([_entry("run-gone")])

    assert tx.inserted_rows is None
    # 校验发生在 advisory lock 之后（锁内），与删除路径串行化才有意义。
    assert [kind for kind, _ in tx.events] == ["lock", "exists_check"]


@pytest.mark.asyncio
async def test_append_entries_checks_existence_inside_lock_then_inserts(monkeypatch):
    service = PostgresLogService()
    tx = _TxRecorder(existing_run_ids={"run-1"})
    monkeypatch.setattr("tortoise.transactions.in_transaction", lambda _name: tx)

    written = await service.append_entries([_entry("run-1")])

    assert written == 1
    assert [kind for kind, _ in tx.events] == ["lock", "exists_check", "insert"]
    assert tx.events[0][1] == [run_commit_lock_key("run-1")]


@pytest.mark.asyncio
async def test_purge_locks_sorted_runs_before_delete(monkeypatch):
    """清扫路径必须取与 append_entries 同一把锁，且按排序取锁防死锁。"""
    transactions: list[_TxRecorder] = []

    def _new_tx(_name):
        tx = _TxRecorder()
        transactions.append(tx)
        return tx

    monkeypatch.setattr("tortoise.transactions.in_transaction", _new_tx)

    removed = await purge_task_logs_for_runs(["run-b", "run-a", "run-b", ""])

    assert removed == _TWO_RUNS
    assert len(transactions) == 1
    events = transactions[0].events
    assert [kind for kind, _ in events] == ["lock", "lock", "delete"]
    # 排序取锁：run-a 在 run-b 之前，与 append_entries 的 sorted(run_ids) 锁序一致
    assert events[0][1] == [run_commit_lock_key("run-a")]
    assert events[1][1] == [run_commit_lock_key("run-b")]
    assert events[2][1] == ["run-a", "run-b"]


@pytest.mark.asyncio
async def test_purge_bounds_locks_per_transaction(monkeypatch):
    """批量删除 run 数很大时按批开事务，单事务 advisory lock 数有界。"""
    transactions: list[_TxRecorder] = []

    def _new_tx(_name):
        tx = _TxRecorder()
        transactions.append(tx)
        return tx

    monkeypatch.setattr("tortoise.transactions.in_transaction", _new_tx)

    total = TASK_LOG_PURGE_BATCH_SIZE + 1
    await purge_task_logs_for_runs([f"run-{index:04d}" for index in range(total)])

    assert len(transactions) == _TWO_TRANSACTIONS
    first_locks = [event for event in transactions[0].events if event[0] == "lock"]
    second_locks = [event for event in transactions[1].events if event[0] == "lock"]
    assert len(first_locks) == TASK_LOG_PURGE_BATCH_SIZE
    assert len(second_locks) == 1
