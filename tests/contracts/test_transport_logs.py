"""
Log contract — `send_log`, `send_log_batch`, `send_log_chunk`.
"""

from __future__ import annotations

import base64
from datetime import datetime

import pytest

pytestmark = pytest.mark.asyncio


async def test_send_log_single(transport, fresh_ids, redis_admin):
    from antcode_worker.transport.base import LogMessage

    log = LogMessage(
        run_id=fresh_ids.run_id,
        log_type="stdout",
        content="hello world",
        timestamp=datetime.now(),
        sequence=1,
    )
    ok = await transport.send_log(log)
    assert ok is True

    if redis_admin is not None:
        keys = transport._test_keys  # type: ignore[attr-defined]
        length = await redis_admin.xlen(keys.log_stream(fresh_ids.run_id))
        assert length == 1


async def test_send_log_batch_appends_n_entries(transport, fresh_ids, redis_admin):
    """Sending N logs in one batch must grow the log stream by exactly N."""
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
    ok = await transport.send_log_batch(batch)
    assert ok is True

    if redis_admin is not None:
        keys = transport._test_keys  # type: ignore[attr-defined]
        length = await redis_admin.xlen(keys.log_stream(fresh_ids.run_id))
        assert length == 5


async def test_send_log_batch_empty_is_noop(transport):
    """Passing an empty list must succeed without raising."""
    ok = await transport.send_log_batch([])
    assert ok is True


async def test_send_log_chunk_concat_round_trips(
    transport, fresh_ids, redis_admin
):
    """When you send chunks of bytes, the concatenated payload from the
    storage stream must be byte-equal to the original."""
    payload = b"".join(bytes([i % 256]) for i in range(1024))
    chunks = [payload[i : i + 256] for i in range(0, len(payload), 256)]

    offset = 0
    for idx, chunk in enumerate(chunks):
        is_final = idx == len(chunks) - 1
        ok = await transport.send_log_chunk(
            run_id=fresh_ids.run_id,
            log_type="stdout",
            data=chunk,
            offset=offset,
            is_final=is_final,
        )
        assert ok is True
        offset += len(chunk)

    if redis_admin is not None:
        keys = transport._test_keys  # type: ignore[attr-defined]
        entries = await redis_admin.xrange(
            keys.log_chunk_stream(fresh_ids.run_id), count=1000
        )
        assert len(entries) == len(chunks)
        # Sort by offset just in case implementation reorders.
        sorted_entries = sorted(entries, key=lambda e: int(e[1]["offset"]))
        reconstructed = b"".join(
            base64.b64decode(e[1]["data"]) for e in sorted_entries
        )
        assert reconstructed == payload
        # The final chunk's `is_final` field must be truthy.
        assert sorted_entries[-1][1]["is_final"].lower() in ("true", "1")


async def test_send_log_chunk_marks_intermediate_not_final(
    transport, fresh_ids, redis_admin
):
    """`is_final=False` chunks must not be flagged as final."""
    ok = await transport.send_log_chunk(
        run_id=fresh_ids.run_id,
        log_type="stdout",
        data=b"intermediate",
        offset=0,
        is_final=False,
    )
    assert ok is True

    if redis_admin is not None:
        keys = transport._test_keys  # type: ignore[attr-defined]
        entries = await redis_admin.xrange(
            keys.log_chunk_stream(fresh_ids.run_id), count=10
        )
        assert len(entries) == 1
        _id, fields = entries[0]
        assert fields["is_final"].lower() in ("false", "0")
