"""Task-log readers must stop at the storage boundary, not after materialization."""

from __future__ import annotations

from unittest.mock import AsyncMock

import antcode_core.application.services.logs.task_log_readers as readers
import pytest
from antcode_core.application.services.logs.postgres_log_service import PostgresLogEntry

EXPECTED_PAGE_COUNT = 2


def _entry(storage_id: int, content: str, log_type: str = "stdout") -> PostgresLogEntry:
    return PostgresLogEntry(
        run_id="run-1",
        log_type=log_type,
        content=content,
        storage_id=storage_id,
    )


@pytest.mark.asyncio
async def test_execution_logs_use_stable_keyset_pages(monkeypatch):
    first_page = [_entry(index, str(index)) for index in range(1, readers.LOG_READ_PAGE_SIZE + 1)]
    second_page = [_entry(readers.LOG_READ_PAGE_SIZE + 1, "last")]
    pages = AsyncMock(side_effect=[first_page, second_page])
    monkeypatch.setattr(readers.postgres_log_service, "latest_snapshot_id", AsyncMock(return_value=100))
    monkeypatch.setattr(readers.postgres_log_service, "list_history_window_page", pages)

    output, error, found = await readers.read_postgres_execution_logs("run-1")

    assert found is True
    assert error == ""
    assert output.endswith("last")
    assert pages.await_count == EXPECTED_PAGE_COUNT
    assert pages.await_args_list[0].kwargs["after"] is None
    assert pages.await_args_list[1].kwargs["after"] == readers.LOG_READ_PAGE_SIZE


@pytest.mark.asyncio
async def test_execution_logs_stop_inside_page_when_byte_budget_is_reached(monkeypatch):
    real_collector = readers.BoundedLogCollector
    monkeypatch.setattr(
        readers,
        "BoundedLogCollector",
        lambda *args, **kwargs: real_collector(max_bytes=10, max_entries=100),
    )
    pages = AsyncMock(return_value=[_entry(1, "123456"), _entry(2, "abcdef"), _entry(3, "unread")])
    monkeypatch.setattr(readers.postgres_log_service, "latest_snapshot_id", AsyncMock(return_value=3))
    monkeypatch.setattr(readers.postgres_log_service, "list_history_window_page", pages)

    output, error, found = await readers.read_postgres_execution_logs("run-1")

    assert found is True
    assert error == ""
    assert "123456" in output
    assert "unread" not in output
    assert readers.LOG_TRUNCATED_MARKER in output
    pages.assert_awaited_once()


@pytest.mark.asyncio
async def test_read_log_with_line_limit_reads_tail_without_truncation_marker(monkeypatch):
    page = [_entry(5, "five"), _entry(4, "four"), _entry(3, "three")]
    monkeypatch.setattr(readers.postgres_log_service, "latest_snapshot_id", AsyncMock(return_value=5))
    latest_page = AsyncMock(return_value=page)
    monkeypatch.setattr(readers.postgres_log_service, "list_latest_page", latest_page)

    output = await readers.read_postgres_log_text("run-1", "stdout", 2)

    assert output == "four\nfive"
    assert readers.LOG_TRUNCATED_MARKER not in output
    latest_page.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_storage_id_fails_instead_of_restarting_page(monkeypatch):
    full_page = [_entry(0, "line") for _ in range(readers.LOG_READ_PAGE_SIZE)]
    monkeypatch.setattr(readers.postgres_log_service, "latest_snapshot_id", AsyncMock(return_value=3))
    monkeypatch.setattr(readers.postgres_log_service, "list_history_window_page", AsyncMock(return_value=full_page))

    with pytest.raises(RuntimeError, match="invalid storage_id"):
        await readers.read_postgres_execution_logs("run-1")


@pytest.mark.asyncio
async def test_redis_reader_stops_without_fetching_another_page(monkeypatch):
    real_collector = readers.BoundedLogCollector
    monkeypatch.setattr(
        readers,
        "BoundedLogCollector",
        lambda *args, **kwargs: real_collector(max_bytes=5, max_entries=100),
    )
    redis = AsyncMock()
    redis.xread.return_value = [
        (
            b"stream",
            [
                (b"1-0", {b"run_id": b"run-1", b"log_type": b"stdout", b"content": b"12345"}),
                (b"2-0", {b"run_id": b"run-1", b"log_type": b"stdout", b"content": b"unread"}),
            ],
        )
    ]
    monkeypatch.setattr(readers.settings, "REDIS_URL", "redis://configured")
    monkeypatch.setattr(readers, "_redis_log_sources", AsyncMock(return_value=(redis, ["stream-a", "stream-b"])))

    output, error = await readers.read_redis_execution_logs("run-1")

    assert error == ""
    assert output.startswith("12345")
    assert "unread" not in output
    assert readers.LOG_TRUNCATED_MARKER in output
    redis.xread.assert_awaited_once()


@pytest.mark.parametrize("namespace", ["antcode", "tenant-a"])
@pytest.mark.asyncio
async def test_redis_log_fallback_reads_the_key_the_writers_write(monkeypatch, namespace):
    """回落读取的 ingest key 必须与写入侧同源，不允许各自手拼。

    写入侧（gateway LogHandler / Direct log_ingest_fence / master ingest loop）
    统一走 ``log_ingest_stream_key``，它带 Cluster hash tag ``{ns}:log:ingest``。
    历史实现在读取侧手拼 ``f"{ns}:log:ingest"``，与写入侧永远不可能相等，
    PG 尚未 flush 的窗口内日志回落恒为空。这里把两侧结构性绑死。
    """
    from antcode_core.infrastructure.redis.control_plane import log_ingest_stream_key

    monkeypatch.setattr(readers.settings, "REDIS_NAMESPACE", namespace)
    monkeypatch.setattr(
        "antcode_core.infrastructure.redis.client.get_redis_client",
        AsyncMock(return_value=object()),
    )

    _redis, sources = await readers._redis_log_sources("run-1")

    assert log_ingest_stream_key(namespace) in sources
