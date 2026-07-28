"""Log storage failures must not be presented as empty task output."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_core.application.services.logs.task_log_service as log_module
import pytest
from antcode_core.application.services.logs.task_log_service import TaskLogService


def _service(sequences: list[int] | None = None) -> tuple[TaskLogService, AsyncMock]:
    allocate = AsyncMock(return_value=sequences or [1])
    return TaskLogService(SimpleNamespace(allocate=allocate)), allocate


@pytest.mark.asyncio
async def test_pg_failure_uses_available_redis_logs(monkeypatch):
    service, _ = _service()
    monkeypatch.setattr(
        log_module.postgres_log_service,
        "list_entries",
        AsyncMock(side_effect=RuntimeError("postgres unavailable")),
    )
    monkeypatch.setattr(service, "_get_redis_stream_logs", AsyncMock(return_value=("cached output", "")))

    result = await service.get_execution_logs("run-1")

    assert result == {"output": "cached output", "error": ""}


@pytest.mark.asyncio
async def test_pg_failure_without_fallback_is_exposed(monkeypatch):
    service, _ = _service()
    monkeypatch.setattr(
        log_module.postgres_log_service,
        "list_entries",
        AsyncMock(side_effect=RuntimeError("postgres unavailable")),
    )
    monkeypatch.setattr(service, "_get_redis_stream_logs", AsyncMock(return_value=("", "")))

    with pytest.raises(RuntimeError, match="日志存储不可用"):
        await service.get_execution_logs("run-1")


@pytest.mark.asyncio
async def test_read_log_exposes_postgres_failure(monkeypatch):
    service, _ = _service()
    monkeypatch.setattr(
        log_module.postgres_log_service,
        "list_entries",
        AsyncMock(side_effect=RuntimeError("postgres unavailable")),
    )

    with pytest.raises(RuntimeError, match="PG 日志读取失败"):
        await service.read_log("run-1", "stdout")


@pytest.mark.asyncio
async def test_write_log_uses_allocated_positive_sequence(monkeypatch):
    service, allocate = _service([41])
    append = AsyncMock(return_value=1)
    monkeypatch.setattr(log_module.postgres_log_service, "append_entries", append)

    await service.write_log("run-1", "STDOUT", "line")

    allocate.assert_awaited_once_with("run-1", "stdout", 1)
    entry = append.await_args.args[0][0]
    assert entry.sequence == 41


@pytest.mark.asyncio
async def test_write_log_exposes_postgres_failure(monkeypatch):
    service, _ = _service([1])
    monkeypatch.setattr(
        log_module.postgres_log_service,
        "append_entries",
        AsyncMock(side_effect=RuntimeError("postgres unavailable")),
    )

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await service.write_log("run-1", "stdout", "line")


@pytest.mark.asyncio
async def test_write_log_rejects_zero_persisted_rows(monkeypatch):
    service, _ = _service([1])
    monkeypatch.setattr(log_module.postgres_log_service, "append_entries", AsyncMock(return_value=0))

    with pytest.raises(RuntimeError, match="未持久化唯一行"):
        await service.write_log("run-1", "stdout", "line")


@pytest.mark.asyncio
async def test_write_log_rejects_non_positive_allocator_result(monkeypatch):
    service, _ = _service([0])
    append = AsyncMock()
    monkeypatch.setattr(log_module.postgres_log_service, "append_entries", append)

    with pytest.raises(RuntimeError, match="日志序号分配结果非法"):
        await service.write_log("run-1", "stdout", "line")

    append.assert_not_awaited()


def test_decode_stream_message_rejects_malformed_protobuf():
    service, _ = _service()

    with pytest.raises(ValueError, match="protobuf 解码失败"):
        service._decode_stream_message({b"p": b"not-a-log-batch"}, "run-1")


def test_decode_stream_message_treats_empty_p_field_as_protobuf():
    service, _ = _service()

    assert service._decode_stream_message({b"p": b"", b"content": b"legacy"}, "run-1") == []
