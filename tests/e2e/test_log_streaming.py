"""
日志流端到端测试

覆盖基础日志链路：API 查询与 SSE 实时推送。
"""

import httpx
import pytest

from .conftest import requires_postgres, requires_redis
from .helpers import get_worker, trigger_task
from .log_client import get_logs, wait_for_realtime_log_and_status
from .run_scenarios import RunScenario, provision_scenario, trigger_and_wait, wait_for_status

REALTIME_LOG_DELAY_SECONDS = 10.0
RUNNING_STATUSES = frozenset({"running"})
SUCCESS_STATUSES = frozenset({"success"})


@requires_postgres
@requires_redis
@pytest.mark.asyncio
async def test_log_api_raw_and_structured(e2e_config, e2e_token):
    """日志 API 可读取 raw/structured"""
    async with httpx.AsyncClient(
        base_url=e2e_config.web_api_url,
        timeout=e2e_config.http_timeout,
    ) as client:
        worker = await get_worker(client, e2e_token, e2e_config.worker_id)
        async with provision_scenario(
            client,
            e2e_token,
            worker["id"],
            config=e2e_config,
            scenario=RunScenario(expected_status="success"),
        ) as resources:
            run = await trigger_and_wait(client, e2e_token, resources, config=e2e_config)
            run_id = run["run_id"]
            log_token = resources.source.expected_log_token

            raw_logs = await get_logs(client, e2e_token, run_id, log_format="raw")
            raw_content = raw_logs.get("raw_content", "")
            assert log_token in raw_content

            structured_logs = await get_logs(client, e2e_token, run_id, log_format="structured")
            items = (structured_logs.get("structured_data") or {}).get("items", [])
            assert items, "结构化日志为空"


@requires_postgres
@requires_redis
@pytest.mark.asyncio
async def test_sse_log_push(e2e_config, e2e_token):
    """任务运行中建立 SSE 订阅并收到增量日志。"""
    async with httpx.AsyncClient(
        base_url=e2e_config.web_api_url,
        timeout=e2e_config.http_timeout,
    ) as client:
        worker = await get_worker(client, e2e_token, e2e_config.worker_id)
        async with provision_scenario(
            client,
            e2e_token,
            worker["id"],
            config=e2e_config,
            scenario=RunScenario(
                expected_status="success",
                log_delay_seconds=REALTIME_LOG_DELAY_SECONDS,
            ),
        ) as resources:
            await trigger_task(client, e2e_token, resources.task["id"])
            run = await wait_for_status(
                client,
                e2e_token,
                resources.task["id"],
                expected=RUNNING_STATUSES,
                timeout=e2e_config.poll_timeout,
                interval=e2e_config.poll_interval,
            )
            run_id = run["run_id"]
            log_token = resources.source.expected_log_token

            message, status_message = await wait_for_realtime_log_and_status(
                client,
                run_id,
                token=e2e_token,
                expected_content=log_token,
                terminal_statuses=SUCCESS_STATUSES,
                timeout=e2e_config.poll_timeout,
            )
            assert message["type"] == "log_line"
            assert log_token in (message.get("data") or {})["content"]
            assert (message.get("data") or {})["source"] == "realtime"
            assert status_message["type"] == "run_status"
            assert (status_message.get("data") or {})["status"] == "success"
            await wait_for_status(
                client,
                e2e_token,
                resources.task["id"],
                expected=SUCCESS_STATUSES,
                timeout=e2e_config.poll_timeout,
                interval=e2e_config.poll_interval,
            )
