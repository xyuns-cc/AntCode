"""Direct 与 Gateway 的日志摄取生产端契约。"""

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from antcode_contracts import data_pb2
from antcode_gateway.handlers.logs import LogHandler
from antcode_worker.transport.base import LogMessage
from antcode_worker.transport.redis.transport import RedisTransport


class _Redis:
    def __init__(self) -> None:
        self.eval_script = ""

    async def eval(self, script: str, *_args: Any) -> list[Any]:
        self.eval_script = script
        return [1, b"1-0"]


class _DirectControl:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    async def report_log_batch(self, payload: bytes) -> bool:
        self.payloads.append(payload)
        return True


def _log_batch() -> data_pb2.LogBatch:
    # P1-GW-03: batch_id 是重发去重幂等键，Gateway 入口强制存在。
    # P1-GW-05: 且必须与 entries 的内容哈希一致（服务端复核）。
    from antcode_core.common.log_batch_hash import deterministic_batch_id

    batch = data_pb2.LogBatch(
        worker_id="worker-1",
        lease_id="lease-1",
        entries=[
            data_pb2.LogEntry(
                run_id="run-1",
                log_type=data_pb2.LOG_TYPE_STDOUT,
                content="line",
                sequence=1,
            )
        ],
    )
    batch.batch_id = deterministic_batch_id(batch.worker_id, batch.entries)
    return batch


@pytest.mark.asyncio
async def test_direct_log_ingest_uses_trusted_control_plane() -> None:
    redis = _Redis()
    control = _DirectControl()
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._redis = redis
    transport._direct_control = control
    transport._running = True
    transport._lease_id = "lease-1"
    transport._require_current_generation = AsyncMock()
    log = LogMessage(
        run_id="run-1",
        log_type="stdout",
        content="line",
        timestamp=datetime.now(),
        sequence=1,
    )

    assert await transport.send_log(log) is True
    assert len(control.payloads) == 1
    assert redis.eval_script == ""


@pytest.mark.asyncio
async def test_gateway_log_ingest_has_no_producer_retention() -> None:
    redis = _Redis()
    handler = LogHandler(redis_client=redis)

    assert await handler.handle_log_batch(_log_batch()) is True
    assert "MAXLEN" not in redis.eval_script.upper()
