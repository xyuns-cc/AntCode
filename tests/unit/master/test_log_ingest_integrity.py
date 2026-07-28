"""Generation-aware validation for Redis log-ingest frames."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import data_pb2
from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD
from antcode_master.ingester.log_ingest_integrity import (
    BoundedLogBatchCodec,
    _mismatched_run_bindings,
    _require_generation_backlog,
)
from antcode_master.ingester.log_ingest_loop import LogIngestLoop
from antcode_master.ingester.log_ingest_message import InvalidLogBatchError

integrity_module = importlib.import_module("antcode_master.ingester.log_ingest_integrity")


def test_log_ingest_codec_rejects_oversized_frame_before_proto_parse() -> None:
    codec = BoundedLogBatchCodec(max_frame_bytes=16)

    with pytest.raises(ValueError, match="超过 16 bytes"):
        codec.decode({PROTO_FIELD: b"not-protobuf-at-all"})


def test_log_ingest_codec_rejects_extra_redis_fields() -> None:
    codec = BoundedLogBatchCodec()
    payload = data_pb2.LogBatch().SerializeToString()

    with pytest.raises(ValueError, match="只能包含 p 字段"):
        codec.decode({PROTO_FIELD: payload, b"unexpected": b"value"})


@pytest.mark.parametrize(
    ("bindings", "error"),
    [({}, "不存在"), ({"run-1": (8, "lease-1")}, "不属于")],
)
def test_log_integrity_rejects_missing_or_foreign_run_binding(bindings, error) -> None:
    with pytest.raises(InvalidLogBatchError, match=error):
        _mismatched_run_bindings(7, "lease-1", {"run-1"}, bindings=bindings)


def test_current_generation_first_log_needs_no_history() -> None:
    mismatched = _mismatched_run_bindings(
        7,
        "lease-3",
        {"run-1"},
        bindings={"run-1": (7, "lease-3")},
    )

    assert mismatched == set()


def test_unbound_run_cannot_be_bound_by_log_ingest() -> None:
    with pytest.raises(InvalidLogBatchError, match="ownership claim"):
        _mismatched_run_bindings(7, "lease-1", {"run-1"}, bindings={"run-1": (7, None)})


@pytest.mark.asyncio
async def test_old_generation_backlog_before_cutoff_is_accepted(monkeypatch) -> None:
    _install_history(monkeypatch, {"lease-1": "20-0"})

    await _require_generation_backlog(7, "lease-1", {"run-1"}, msg_id="19-9")


@pytest.mark.asyncio
async def test_old_generation_log_after_cutoff_is_rejected(monkeypatch) -> None:
    _install_history(monkeypatch, {"lease-1": "20-0"})

    with pytest.raises(InvalidLogBatchError, match="cutoff"):
        await _require_generation_backlog(7, "lease-1", {"run-1"}, msg_id="20-1")


@pytest.mark.asyncio
async def test_l1_and_l2_backlogs_survive_l3_takeover(monkeypatch) -> None:
    history = _install_history(
        monkeypatch,
        {
            "lease-1": "20-0",
            "lease-2": "30-0",
        },
    )

    await _require_generation_backlog(7, "lease-1", {"run-1"}, msg_id="19-9")
    await _require_generation_backlog(7, "lease-2", {"run-1"}, msg_id="29-9")

    queried_leases = [call.kwargs["lease_id"] for call in history.filter.call_args_list]
    assert queried_leases == ["lease-1", "lease-2"]


@pytest.mark.asyncio
async def test_invalid_log_lease_is_dead_lettered_and_acknowledged() -> None:
    invalid = AsyncMock(side_effect=InvalidLogBatchError("old lease"))
    loop = LogIngestLoop(integrity_validator=invalid)
    loop._dead_letter_invalid_batch = AsyncMock()
    message = SimpleNamespace(
        msg_id="2-0",
        decode_error=None,
        payload=data_pb2.LogBatch(entries=[data_pb2.LogEntry(run_id="run-1")]),
    )

    assert await loop._process_message(message) is True
    loop._dead_letter_invalid_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_run_id_is_dead_lettered_before_storage() -> None:
    loop = LogIngestLoop(sequence_allocator=SimpleNamespace(allocate=AsyncMock()))
    loop._dead_letter_invalid_batch = AsyncMock()
    message = SimpleNamespace(
        msg_id="2-0",
        decode_error=None,
        payload=data_pb2.LogBatch(
            worker_id="worker-1",
            lease_id="lease-1",
            entries=[data_pb2.LogEntry(run_id=" run-1")],
        ),
    )

    assert await loop._process_message(message) is True
    loop._dead_letter_invalid_batch.assert_awaited_once()
    loop._sequence_allocator.allocate.assert_not_awaited()


def _install_history(monkeypatch, cutoffs: dict[str, str]) -> SimpleNamespace:
    def filter_history(**filters):
        cutoff = cutoffs.get(filters["lease_id"])
        rows = [("run-1", cutoff)] if cutoff is not None else []
        return SimpleNamespace(values_list=AsyncMock(return_value=rows))

    history = SimpleNamespace(filter=MagicMock(side_effect=filter_history))
    monkeypatch.setattr(integrity_module, "TaskRunLeaseGeneration", history)
    return history
