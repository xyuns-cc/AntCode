"""
Worker 生命周期端到端测试

覆盖基础心跳能力：在线状态与心跳更新。
"""

import asyncio

import httpx
import pytest

from .conftest import requires_postgres, requires_redis
from .helpers import get_worker, parse_heartbeat

HEARTBEAT_UPDATE_TIMEOUT_SECONDS = 40.0
HEARTBEAT_POLL_INTERVAL_SECONDS = 2.0


@requires_postgres
@requires_redis
@pytest.mark.asyncio
async def test_worker_heartbeat(e2e_config, e2e_token):
    """Worker 在线与心跳更新"""
    async with httpx.AsyncClient(
        base_url=e2e_config.web_api_url,
        timeout=e2e_config.http_timeout,
    ) as client:
        worker = await get_worker(client, e2e_token, e2e_config.worker_id)
        assert worker.get("status") == "online"

        first_heartbeat = parse_heartbeat(worker)
        assert first_heartbeat is not None

        loop = asyncio.get_running_loop()
        deadline = loop.time() + HEARTBEAT_UPDATE_TIMEOUT_SECONDS
        while loop.time() < deadline:
            await asyncio.sleep(HEARTBEAT_POLL_INTERVAL_SECONDS)
            latest = await get_worker(client, e2e_token, worker.get("id"))
            latest_heartbeat = parse_heartbeat(latest)
            if latest_heartbeat and latest_heartbeat > first_heartbeat:
                break
        else:
            pytest.fail("心跳未在完整上报周期内更新")
