"""
日志流端到端测试

覆盖基础日志链路：API 查询与 WebSocket 推送。
"""

import httpx
import pytest

from .conftest import requires_mysql, requires_redis
from .helpers import (
    create_execution_context,
    get_logs,
    get_worker,
    login,
    wait_for_websocket_message,
)


@requires_mysql
@requires_redis
@pytest.mark.asyncio
async def test_log_api_raw_and_structured(e2e_config):
    """日志 API 可读取 raw/structured"""
    async with httpx.AsyncClient(
        base_url=e2e_config.web_api_url,
        timeout=e2e_config.http_timeout,
    ) as client:
        token = await login(client, e2e_config)
        worker = await get_worker(client, token, e2e_config.worker_id)
        context = await create_execution_context(client, token, worker["id"], e2e_config)
        execution_id = context["execution"]["execution_id"]
        log_token = context["log_token"]

        raw_logs = await get_logs(client, token, execution_id, log_format="raw")
        raw_content = raw_logs.get("raw_content", "")
        assert log_token in raw_content

        structured_logs = await get_logs(client, token, execution_id, log_format="structured")
        items = (structured_logs.get("structured_data") or {}).get("items", [])
        assert items, "结构化日志为空"


@requires_mysql
@requires_redis
@pytest.mark.asyncio
async def test_websocket_log_push(e2e_config):
    """WebSocket 日志可收到消息"""
    async with httpx.AsyncClient(
        base_url=e2e_config.web_api_url,
        timeout=e2e_config.http_timeout,
    ) as client:
        token = await login(client, e2e_config)
        worker = await get_worker(client, token, e2e_config.worker_id)
        context = await create_execution_context(client, token, worker["id"], e2e_config)
        execution_id = context["execution"]["execution_id"]
        log_token = context["log_token"]

        message = await wait_for_websocket_message(e2e_config, execution_id, token)
        msg_type = message.get("type")
        assert msg_type in {
            "execution_status",
            "log_line",
            "historical_logs_start",
            "historical_logs_end",
            "no_historical_logs",
        }
        if msg_type == "log_line":
            content = (message.get("data") or {}).get("content", "")
            assert log_token in content
