import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.logs.postgres_log_service import PostgresLogEntry
from antcode_core.application.services.workers.distributed_log_service import DistributedLogService

service_module = importlib.import_module("antcode_core.application.services.workers.distributed_log_service")


@pytest.mark.asyncio
async def test_append_logs_enqueues_database_authoritative_entries(monkeypatch):
    allocator = SimpleNamespace(allocate=AsyncMock(return_value=[8, 9]))
    service = DistributedLogService(allocator)
    persisted = [_persisted_entry(8, 41), _persisted_entry(9, 42)]
    loader = AsyncMock(return_value=persisted)
    enqueue = AsyncMock()
    monkeypatch.setattr(
        service_module.postgres_task_log_service,
        "append_entries",
        AsyncMock(return_value=2),
    )
    monkeypatch.setattr(service_module, "list_persisted_log_sequences", loader)
    monkeypatch.setattr(service, "_has_stream_connections", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_enqueue_push_logs", enqueue)

    await service.append_logs("run-1", "stdout", ["first", "second"])

    requested = loader.await_args.args[0]
    assert [entry.sequence for entry in requested] == [8, 9]
    enqueue.assert_awaited_once_with("run-1", persisted)


@pytest.mark.asyncio
async def test_push_log_passes_storage_id_to_notifier():
    service = DistributedLogService(SimpleNamespace(allocate=AsyncMock()))
    notifier = SimpleNamespace(send_log=AsyncMock())
    service.set_notifier(notifier)

    await service._push_log(_persisted_entry(8, 41))

    assert notifier.send_log.await_args.kwargs["storage_id"] == 41


@pytest.mark.asyncio
async def test_append_logs_rejects_unexpected_write_count(monkeypatch):
    service = DistributedLogService(SimpleNamespace(allocate=AsyncMock(return_value=[8])))
    monkeypatch.setattr(
        service_module.postgres_task_log_service,
        "append_entries",
        AsyncMock(return_value=0),
    )
    loader = AsyncMock()
    monkeypatch.setattr(service_module, "list_persisted_log_sequences", loader)

    with pytest.raises(RuntimeError, match="日志写入数量异常"):
        await service.append_log("run-1", "stdout", "line")

    loader.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_not_extended_when_append_fails(monkeypatch):
    # L4 回归：PG append 抛错时，热缓存不得留下未落库的"幽灵"行
    # （缓存只在 append 成功后追加）。
    service = DistributedLogService(SimpleNamespace(allocate=AsyncMock(return_value=[1])))
    monkeypatch.setattr(
        service_module.postgres_task_log_service,
        "append_entries",
        AsyncMock(side_effect=RuntimeError("pg down")),
    )

    with pytest.raises(RuntimeError, match="pg down"):
        await service.append_log("run-1", "stdout", "line")

    assert service._cache_key("run-1", "stdout") not in service._log_cache


@pytest.mark.asyncio
async def test_stale_run_cache_is_swept_after_ttl(monkeypatch):
    # L2 回归：master/reconcile 直接写 DB 的终态不经 update_task_status，
    # 其热缓存 / _task_status 须由 TTL 惰性清扫，不能在长驻进程内永久泄漏。
    clock = [1000.0]
    monkeypatch.setattr(service_module, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(service_module, "CACHE_TTL_SECONDS", 100.0)
    monkeypatch.setattr(service_module, "SWEEP_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(
        service_module.postgres_task_log_service,
        "append_entries",
        AsyncMock(side_effect=lambda entries: len(entries)),
    )
    allocator = SimpleNamespace(allocate=AsyncMock(side_effect=lambda run_id, log_type, n: list(range(1, n + 1))))
    service = DistributedLogService(allocator)

    await service.append_logs("run-stale", "stdout", ["x"])
    assert service._cache_key("run-stale", "stdout") in service._log_cache
    assert "run-stale" in service._last_touch

    clock[0] = 1000.0 + 200.0  # 超过 TTL
    await service.append_logs("run-live", "stdout", ["y"])

    # 陈旧 run 被清扫，活跃 run 保留。
    assert service._cache_key("run-stale", "stdout") not in service._log_cache
    assert "run-stale" not in service._last_touch
    assert service._cache_key("run-live", "stdout") in service._log_cache
    assert "run-live" in service._last_touch


def _persisted_entry(sequence: int, storage_id: int) -> PostgresLogEntry:
    return PostgresLogEntry(
        run_id="run-1",
        log_type="stdout",
        content=f"line-{sequence}",
        sequence=sequence,
        level="INFO",
        source="worker_report",
        storage_id=storage_id,
    )
