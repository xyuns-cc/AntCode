import pytest
from antcode_gateway.handlers.heartbeat import HeartbeatData, HeartbeatHandler


@pytest.mark.asyncio
async def test_heartbeat_requires_redis_client():
    handler = HeartbeatHandler()
    handler._get_redis_client = _missing_redis_client

    with pytest.raises(RuntimeError, match="Redis"):
        await handler.handle(HeartbeatData(worker_id="worker-1"))


async def _missing_redis_client():
    return None
