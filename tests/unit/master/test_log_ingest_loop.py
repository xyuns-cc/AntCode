"""Log ingest loop current Proto behavior."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_contracts import data_pb2
from antcode_core.application.services.logs.postgres_log_service import PostgresLogEntry
from antcode_core.infrastructure.redis.sse_event_stream import SSEEventPublishError
from antcode_master.ingester.log_ingest_loop import LogIngestLoop

loop_module = importlib.import_module("antcode_master.ingester.log_ingest_loop")


@pytest.fixture(autouse=True)
def _allow_existing_log_batches(monkeypatch):
    monkeypatch.setattr(loop_module, "require_log_batch_integrity", AsyncMock())


class _IdempotentLogStore:
    def __init__(self):
        self.append_calls = 0
        self.rows: dict[str, PostgresLogEntry] = {}

    async def append_entries(self, entries):
        self.append_calls += 1
        for entry in entries:
            if not entry.event_id:
                raise AssertionError("test store requires event_id")
            self.rows.setdefault(entry.event_id, entry)
        return len(entries)

    async def load(self, event_ids):
        return [self.rows[event_id] for event_id in event_ids]


def _log_message(msg_id: str = "7-0") -> SimpleNamespace:
    entry = data_pb2.LogEntry(run_id="run-1", log_type=data_pb2.LOG_TYPE_STDOUT, content="stable")
    return SimpleNamespace(msg_id=msg_id, decode_error=None, payload=data_pb2.LogBatch(entries=[entry]))


@pytest.mark.asyncio
async def test_handle_batch_persists_proto_entries(monkeypatch):
    append = AsyncMock()
    monkeypatch.setattr(loop_module.postgres_log_service, "append_entries", append)
    allocator = SimpleNamespace(allocate=AsyncMock(return_value=[42]))
    loader = AsyncMock(side_effect=lambda _event_ids: append.await_args.args[0])
    publisher = AsyncMock()
    batch = data_pb2.LogBatch(
        worker_id="worker-1",
        entries=[
            data_pb2.LogEntry(
                run_id="run-1",
                log_type=data_pb2.LOG_TYPE_STDOUT,
                content="hello",
                sequence=7,
            )
        ],
    )

    await LogIngestLoop(
        sequence_allocator=allocator,
        event_loader=loader,
        line_publisher=publisher,
    )._handle_batch(batch, msg_id="1-0")

    entries = append.await_args.args[0]
    assert len(entries) == 1
    assert entries[0].event_id == "1-0:0"
    assert entries[0].run_id == "run-1"
    assert entries[0].content == "hello"
    assert entries[0].sequence == 42
    allocator.allocate.assert_awaited_once_with("run-1", "stdout", 1)
    loader.assert_awaited_once_with(["1-0:0"])
    publisher.assert_awaited_once_with(entries)


@pytest.mark.asyncio
async def test_handle_batch_keeps_storage_failure_visible(monkeypatch):
    monkeypatch.setattr(
        loop_module.postgres_log_service,
        "append_entries",
        AsyncMock(side_effect=RuntimeError("postgres unavailable")),
    )
    batch = data_pb2.LogBatch(entries=[data_pb2.LogEntry(run_id="run-1", log_type=data_pb2.LOG_TYPE_STDOUT)])

    allocator = SimpleNamespace(allocate=AsyncMock(return_value=[1]))
    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await LogIngestLoop(sequence_allocator=allocator)._handle_batch(batch, msg_id="1-0")


@pytest.mark.asyncio
async def test_dead_letter_bad_frame_exposes_dlq_failure():
    loop = LogIngestLoop()
    client = SimpleNamespace(xadd=AsyncMock(side_effect=RuntimeError("dlq unavailable")))
    loop._stream._get_client = AsyncMock(return_value=client)
    message = SimpleNamespace(msg_id="1-0", decode_error="bad proto", raw_fields={})

    with pytest.raises(RuntimeError, match="dlq unavailable"):
        await loop._dead_letter_bad_frame(message)


@pytest.mark.asyncio
async def test_dead_letter_bad_frame_writes_traceable_entry():
    loop = LogIngestLoop()
    client = SimpleNamespace(xadd=AsyncMock())
    loop._stream._get_client = AsyncMock(return_value=client)
    message = SimpleNamespace(
        msg_id="1-0",
        decode_error="bad proto",
        raw_fields={b"payload": b"broken"},
    )

    await loop._dead_letter_bad_frame(message)

    _, entry = client.xadd.await_args.args
    assert entry["origin_msg_id"] == "1-0"
    assert entry["decode_error"] == "bad proto"
    assert entry["raw_payload"] == "broken"


@pytest.mark.asyncio
async def test_publish_failure_replay_uses_database_authoritative_identity():
    allocated = SimpleNamespace(allocate=AsyncMock(side_effect=[[11], [22]]))
    log_store = SimpleNamespace(append_entries=AsyncMock())
    persisted = PostgresLogEntry(
        run_id="run-1",
        log_type="stdout",
        content="stable",
        sequence=11,
        event_id="7-0:0",
    )
    loader = AsyncMock(return_value=[persisted])
    published: list[tuple[str | None, int]] = []

    async def publish(entries):
        published.append((entries[0].event_id, entries[0].sequence))
        if len(published) == 1:
            raise SSEEventPublishError("redis unavailable")

    loop = LogIngestLoop(
        sequence_allocator=allocated,
        log_store=log_store,
        event_loader=loader,
        line_publisher=publish,
    )
    message = _log_message()

    assert await loop._process_message(message) is False
    assert await loop._process_message(message) is True

    attempted_sequences = [call.args[0][0].sequence for call in log_store.append_entries.await_args_list]
    assert attempted_sequences == [11, 22]
    assert published == [("7-0:0", 11), ("7-0:0", 11)]
    assert loader.await_args_list[0].args[0] == ["7-0:0"]
    assert loader.await_args_list[1].args[0] == ["7-0:0"]


@pytest.mark.asyncio
async def test_ack_then_trims_at_consumer_group_boundary(monkeypatch):
    operations: list[str] = []
    client = object()

    async def ack(*_args):
        operations.append("ack")
        return 1

    async def get_client():
        operations.append("client")
        return client

    async def trim(*args):
        operations.append("trim")
        assert args == (client, "logs", "logs-group")

    loop = LogIngestLoop(stream_key="logs", group_name="logs-group")
    loop._stream = SimpleNamespace(xack=ack, _get_client=get_client)
    monkeypatch.setattr(loop_module, "trim_acknowledged_stream", trim)

    await loop._ack_and_trim(["8-0"])

    assert operations == ["ack", "client", "trim"]


@pytest.mark.asyncio
async def test_ack_trim_is_throttled_and_flushed_on_stop(monkeypatch):
    loop = LogIngestLoop(stream_key="logs", group_name="logs-group")
    loop._stream = SimpleNamespace(
        xack=AsyncMock(return_value=1),
        _get_client=AsyncMock(return_value=object()),
    )
    trim = AsyncMock()
    monkeypatch.setattr(loop_module, "trim_acknowledged_stream", trim)

    await loop._ack_and_trim(["1-0"])
    await loop._ack_and_trim(["2-0"])

    # 第二批落在 30s 节流窗口内：只 ACK 不裁剪，欠账记入 backlog
    trim.assert_awaited_once()
    assert loop._trim_backlog is True

    await loop.stop()

    # 停机时补一次被跳过的裁剪
    assert trim.await_count == 2
    assert loop._trim_backlog is False


@pytest.mark.asyncio
async def test_trim_failure_does_not_repeat_completed_business(monkeypatch):
    store = _IdempotentLogStore()
    loop = LogIngestLoop(stream_key="logs", group_name="logs-group")
    loop._sequence_allocator = SimpleNamespace(allocate=AsyncMock(return_value=[31]))
    loop._log_store = store
    loop._event_loader = store.load
    loop._line_publisher = AsyncMock()
    loop._stream = SimpleNamespace(
        xack=AsyncMock(return_value=1),
        _get_client=AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        loop_module,
        "trim_acknowledged_stream",
        AsyncMock(side_effect=RuntimeError("trim response lost")),
    )
    message = _log_message("9-0")

    await loop._process_messages([message])

    assert store.append_calls == 1
    assert list(store.rows) == ["9-0:0"]
    loop._stream.xack.assert_awaited_once_with("logs", ["9-0"], "logs-group")


@pytest.mark.asyncio
async def test_xack_failure_is_exposed_and_does_not_trim(monkeypatch):
    loop = LogIngestLoop(stream_key="logs", group_name="logs-group")
    loop._stream = SimpleNamespace(
        xack=AsyncMock(side_effect=RuntimeError("ACK response lost")),
        _get_client=AsyncMock(),
    )
    trim = AsyncMock()
    monkeypatch.setattr(loop_module, "trim_acknowledged_stream", trim)

    with pytest.raises(RuntimeError, match="ACK response lost"):
        await loop._ack_and_trim(["10-0"])

    loop._stream._get_client.assert_not_awaited()
    trim.assert_not_awaited()


@pytest.mark.asyncio
async def test_xack_response_loss_replay_keeps_one_business_row(monkeypatch):
    store = _IdempotentLogStore()
    published: list[tuple[str | None, int]] = []

    async def publish(entries):
        published.append((entries[0].event_id, entries[0].sequence))

    loop = LogIngestLoop(
        stream_key="logs",
        group_name="logs-group",
        sequence_allocator=SimpleNamespace(allocate=AsyncMock(side_effect=[[41], [52]])),
        log_store=store,
        event_loader=store.load,
        line_publisher=publish,
    )
    loop._stream = SimpleNamespace(
        xack=AsyncMock(side_effect=[RuntimeError("ACK response lost"), 1]),
        _get_client=AsyncMock(return_value=object()),
    )
    trim = AsyncMock()
    monkeypatch.setattr(loop_module, "trim_acknowledged_stream", trim)
    message = _log_message("10-0")

    with pytest.raises(RuntimeError, match="ACK response lost"):
        await loop._process_messages([message])
    await loop._process_messages([message])

    assert store.append_calls == 2
    assert [(event_id, row.sequence) for event_id, row in store.rows.items()] == [("10-0:0", 41)]
    assert published == [("10-0:0", 41), ("10-0:0", 41)]
    trim.assert_awaited_once()
