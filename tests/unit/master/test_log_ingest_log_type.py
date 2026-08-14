"""P1-SSE-01: 协议外 log_type 的坏帧判定（DLQ）回归。"""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_contracts import data_pb2
from antcode_master.ingester.log_ingest_loop import LogIngestLoop
from antcode_master.ingester.log_ingest_message import build_log_entries

loop_module = importlib.import_module("antcode_master.ingester.log_ingest_loop")

# proto3 开放枚举可携带任意整数值；取一个明显协议外的值做回归。
_UNKNOWN_LOG_TYPE_VALUE = 999


@pytest.fixture(autouse=True)
def _allow_existing_log_batches(monkeypatch):
    monkeypatch.setattr(loop_module, "require_log_batch_integrity", AsyncMock())


def _dlq_loop_with_entry(monkeypatch, entry: data_pb2.LogEntry) -> tuple[LogIngestLoop, SimpleNamespace, AsyncMock]:
    append = AsyncMock()
    monkeypatch.setattr(loop_module.postgres_log_service, "append_entries", append)
    loop = LogIngestLoop(sequence_allocator=SimpleNamespace(allocate=AsyncMock(return_value=[1])))
    loop._dead_letter_invalid_batch = AsyncMock()
    message = SimpleNamespace(
        msg_id="9-0",
        decode_error=None,
        payload=data_pb2.LogBatch(worker_id="worker-1", entries=[entry]),
    )
    return loop, message, append


@pytest.mark.asyncio
async def test_unspecified_log_type_is_dead_lettered_not_persisted(monkeypatch):
    # P1-SSE-01: LOG_TYPE_UNSPECIFIED（缺省值 0）不得按 "unspecified" 落库
    # 污染 task_logs，必须走既有坏帧语义（InvalidLogBatchError -> DLQ）。
    entry = data_pb2.LogEntry(run_id="run-1", content="hello")
    loop, message, append = _dlq_loop_with_entry(monkeypatch, entry)

    assert await loop._process_message(message) is True
    loop._dead_letter_invalid_batch.assert_awaited_once()
    append.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_log_type_enum_is_dead_lettered_not_pel_poisoned(monkeypatch):
    # P1-SSE-01: 此前协议外枚举值在 LogType.Name 抛裸 ValueError，不被
    # InvalidLogBatchError 分支捕获，消息滞留 PEL 无限重试（毒消息）。
    # 现在必须显式判定坏帧进 DLQ 并 ACK。
    entry = data_pb2.LogEntry(run_id="run-1", content="hello")
    entry.log_type = _UNKNOWN_LOG_TYPE_VALUE
    loop, message, append = _dlq_loop_with_entry(monkeypatch, entry)

    assert await loop._process_message(message) is True
    loop._dead_letter_invalid_batch.assert_awaited_once()
    append.assert_not_awaited()


@pytest.mark.asyncio
async def test_stderr_log_is_persisted_with_error_level():
    batch = data_pb2.LogBatch(
        worker_id="worker-1",
        entries=[
            data_pb2.LogEntry(
                run_id="run-1",
                log_type=data_pb2.LogType.LOG_TYPE_STDERR,
                content="failure",
            )
        ],
    )
    allocator = SimpleNamespace(allocate=AsyncMock(return_value=[1]))

    entries = await build_log_entries(batch, "9-0", allocator)

    assert entries[0].log_type == "stderr"
    assert entries[0].level == "ERROR"
