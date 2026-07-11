"""Log ingest loop current Proto behavior."""

import importlib
from unittest.mock import AsyncMock

import pytest
from antcode_contracts import data_pb2
from antcode_master.ingester.log_ingest_loop import LogIngestLoop

loop_module = importlib.import_module("antcode_master.ingester.log_ingest_loop")


@pytest.mark.asyncio
async def test_handle_batch_persists_proto_entries(monkeypatch):
    append = AsyncMock()
    monkeypatch.setattr(loop_module.postgres_log_service, "append_entries", append)
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

    await LogIngestLoop()._handle_batch(batch, msg_id="1-0")

    entries = append.await_args.args[0]
    assert len(entries) == 1
    assert entries[0].event_id == "1-0:0"
    assert entries[0].run_id == "run-1"
    assert entries[0].content == "hello"


@pytest.mark.asyncio
async def test_handle_batch_keeps_storage_failure_visible(monkeypatch):
    monkeypatch.setattr(
        loop_module.postgres_log_service,
        "append_entries",
        AsyncMock(side_effect=RuntimeError("postgres unavailable")),
    )
    batch = data_pb2.LogBatch(entries=[data_pb2.LogEntry(run_id="run-1")])

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await LogIngestLoop()._handle_batch(batch, msg_id="1-0")
