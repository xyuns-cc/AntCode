"""PostgreSQL task log service behavior."""

import pytest
from antcode_core.application.services.logs.postgres_log_service import (
    PostgresLogEntry,
    PostgresLogService,
)


class _TxConnection:
    """模拟 in_transaction 上下文里的连接（P1-SSE-01 提交串行化）。

    P1-DB-03 后 append_entries 还会在锁内 SELECT 校验 TaskRun 存在，
    fake 需按 existing_run_ids 应答存在性查询。
    """

    def __init__(self, existing_run_ids=("run-1",)):
        self.rows = None
        self.lock_calls: list[tuple[str, list]] = []
        self.exists_calls: list[list] = []
        self._existing = set(existing_run_ids)

    async def execute_query(self, sql, params):
        if "pg_advisory_xact_lock" in sql:
            self.lock_calls.append((sql, list(params)))
            return 1, []
        if 'SELECT "run_id"' in sql:
            self.exists_calls.append(list(params))
            found = [{"run_id": run_id} for run_id in params if run_id in self._existing]
            return len(found), found
        return 1, []

    async def execute_many(self, _sql, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_append_entries_includes_event_id_for_deduplication(monkeypatch):
    service = PostgresLogService()
    entry = PostgresLogEntry(
        event_id="worker-log:run-1:3",
        run_id="run-1",
        log_type="stdout",
        content="hello",
        sequence=3,
        source="worker_direct",
    )

    connection = _TxConnection()
    # append_entries 在函数体内 `from tortoise.transactions import in_transaction`
    monkeypatch.setattr("tortoise.transactions.in_transaction", lambda _name: connection)

    assert await service.append_entries([entry]) == 1
    row = connection.rows[0]

    assert row[0] == "worker-log:run-1:3"
    assert row[1] == "run-1"
    # P1-SSE-01: 写入前必须按 run 取 advisory xact lock（提交顺序契约）。
    assert connection.lock_calls, "缺少 per-run advisory lock 调用"
    lock_sql, lock_params = connection.lock_calls[0]
    assert "pg_advisory_xact_lock" in lock_sql
    from antcode_core.application.services.logs.postgres_log_service import run_commit_lock_key

    assert lock_params == [run_commit_lock_key("run-1")]


@pytest.mark.asyncio
async def test_latest_page_uses_snapshot_and_storage_id_keyset(monkeypatch):
    service = PostgresLogService()

    class _Connection:
        calls = []

        async def execute_query(self, sql, params):
            self.calls.append((sql, params))
            return 1, [
                {
                    "id": 40,
                    "run_id": "run-1",
                    "log_type": "stdout",
                    "content": "line",
                    "sequence": 9,
                }
            ]

    connection = _Connection()
    monkeypatch.setattr("tortoise.Tortoise.get_connection", lambda _name: connection)

    entries = await service.list_latest_page(
        "run-1",
        limit=20,
        snapshot_id=50,
        before=41,
    )

    sql, params = connection.calls[0]
    assert '"id" <= $2' in sql
    assert '"id" < $3' in sql
    assert 'ORDER BY "id" DESC' in sql
    assert params == ["run-1", 50, 41, 20]
    assert entries[0].storage_id == 40


@pytest.mark.asyncio
async def test_history_window_page_uses_ascending_inclusive_lower_keyset(monkeypatch):
    service = PostgresLogService()

    class _Connection:
        calls = []

        async def execute_query(self, sql, params):
            self.calls.append((sql, params))
            return 0, []

    connection = _Connection()
    monkeypatch.setattr("tortoise.Tortoise.get_connection", lambda _name: connection)

    await service.list_history_window_page(
        "run-1",
        limit=20,
        snapshot_id=50,
        lower=10,
        after=30,
    )

    sql, params = connection.calls[0]
    assert '"id" <= $2' in sql
    assert '"id" >= $3' in sql
    assert '"id" > $4' in sql
    assert 'ORDER BY "id" ASC' in sql
    assert params == ["run-1", 50, 10, 30, 20]
