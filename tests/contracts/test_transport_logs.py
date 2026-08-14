"""
Log contract — `send_log` and `send_log_batch`.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from antcode_contracts import data_pb2

pytestmark = pytest.mark.asyncio


async def test_send_log_single(transport, fresh_ids, contract_probe):
    from antcode_worker.transport.base import LogMessage

    log = LogMessage(
        run_id=fresh_ids.run_id,
        log_type="stdout",
        content="hello world",
        timestamp=datetime.now(),
        sequence=1,
    )
    assert await transport.claim_run_ownership(fresh_ids.run_id, 30_000)
    ok = await transport.send_log(log)
    assert ok is True

    batches = await contract_probe.log_batches()
    assert len(batches) == 1
    assert batches[0].worker_id == fresh_ids.worker_id
    assert len(batches[0].entries) == 1
    entry = batches[0].entries[0]
    assert entry.run_id == fresh_ids.run_id
    assert entry.log_type == data_pb2.LOG_TYPE_STDOUT
    assert entry.content == "hello world"
    assert entry.sequence == 1


async def test_send_log_batch_writes_one_proto_batch(transport, fresh_ids, contract_probe):
    """Logs for one run share one protobuf batch on the global ingest stream."""
    from antcode_worker.transport.base import LogMessage

    batch = [
        LogMessage(
            run_id=fresh_ids.run_id,
            log_type="stdout",
            content=f"line-{i}",
            timestamp=datetime.now(),
            sequence=i,
        )
        for i in range(5)
    ]
    assert await transport.claim_run_ownership(fresh_ids.run_id, 30_000)
    ok = await transport.send_log_batch(batch)
    assert ok is True

    batches = await contract_probe.log_batches()
    assert len(batches) == 1
    assert [entry.content for entry in batches[0].entries] == [f"line-{i}" for i in range(5)]
    assert [entry.sequence for entry in batches[0].entries] == list(range(5))


async def test_send_log_batch_empty_is_noop(transport):
    """Passing an empty list must succeed without raising."""
    ok = await transport.send_log_batch([])
    assert ok is True
