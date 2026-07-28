import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.logs.postgres_log_service import PostgresLogEntry
from antcode_core.application.services.workers.distributed_log_service import (
    MAX_PUSH_QUEUE_LINES,
    DistributedLogService,
    postgres_task_log_service,
)
from antcode_worker.runtime.locks import RuntimeLock
from antcode_worker.runtime.uv_manager import UVManager


@pytest.mark.asyncio
async def test_distributed_log_terminal_status_releases_hot_state(monkeypatch):
    allocator = SimpleNamespace(allocate=AsyncMock(return_value=[1]))
    service = DistributedLogService(allocator)
    monkeypatch.setattr(postgres_task_log_service, "append_entries", AsyncMock(return_value=1))
    monkeypatch.setattr(service, "_update_runtime_status", AsyncMock())
    monkeypatch.setattr(service, "_push_task_status", AsyncMock())

    await service.update_task_status("run-1", "success")

    assert service._log_cache == {}
    assert service._task_status == {}


@pytest.mark.asyncio
async def test_distributed_log_push_queue_is_bounded():
    service = DistributedLogService(SimpleNamespace(allocate=AsyncMock()))
    service.set_notifier(
        SimpleNamespace(
            send_log=AsyncMock(),
        )
    )

    entry = PostgresLogEntry(
        run_id="run-1",
        log_type="stdout",
        content="line",
        sequence=1,
        timestamp=None,
        level="INFO",
        source="worker_report",
    )
    await service._enqueue_push_logs("run-1", [entry])

    assert service._push_queues["run-1"].maxsize == MAX_PUSH_QUEUE_LINES
    await service.stop()


@pytest.mark.asyncio
async def test_runtime_lock_registry_is_released_after_last_waiter():
    manager = RuntimeLock()
    assert await manager.acquire("hash") is True
    waiter = asyncio.create_task(manager.acquire("hash"))
    await asyncio.sleep(0)

    assert manager._lock_users["hash"] == 2
    await manager.release("hash")
    assert await waiter is True
    assert "hash" in manager._locks

    await manager.release("hash")
    assert "hash" not in manager._locks
    assert "hash" not in manager._lock_users


@pytest.mark.asyncio
async def test_uv_lock_registry_is_released():
    manager = UVManager()

    async with manager._env_operation("env:test"):
        assert "env:test" in manager._locks

    assert manager._locks == {}
    assert manager._lock_users == {}


# 原 ConnectionPool / MessageQueue 的空 run 状态清理断言已随 WebSocket 栈删除，
# 等价的内存卫生用例见 tests/unit/web_api/test_run_stream_broker.py。
