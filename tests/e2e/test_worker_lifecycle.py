"""
Worker 生命周期端到端测试

覆盖基础心跳能力：在线状态与心跳更新。
"""

import asyncio

import httpx
import pytest

from .conftest import requires_mysql, requires_redis
from .helpers import get_worker, login, parse_heartbeat


@requires_mysql
@requires_redis
@pytest.mark.asyncio
async def test_worker_heartbeat(e2e_config):
    """Worker 在线与心跳更新"""
    async with httpx.AsyncClient(
        base_url=e2e_config.web_api_url,
        timeout=e2e_config.http_timeout,
    ) as client:
        token = await login(client, e2e_config)
        worker = await get_worker(client, token, e2e_config.worker_id)
        assert worker.get("status") == "online"

        first_heartbeat = parse_heartbeat(worker)
        assert first_heartbeat is not None

        updated = False
        for _ in range(3):
            await asyncio.sleep(e2e_config.poll_interval * 2)
            latest = await get_worker(client, token, worker.get("id"))
            latest_heartbeat = parse_heartbeat(latest)
            if latest_heartbeat and latest_heartbeat > first_heartbeat:
                updated = True
                break

        assert updated, "心跳未在预期时间内更新"
