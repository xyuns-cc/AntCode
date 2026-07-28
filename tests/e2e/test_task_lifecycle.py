"""
任务生命周期端到端测试

覆盖基本成功路径：创建任务 -> 调度 -> 执行 -> 结果回写
"""

import httpx
import pytest

from .conftest import requires_postgres, requires_redis
from .helpers import get_worker
from .run_scenarios import RunScenario, provision_scenario, trigger_and_wait


@requires_postgres
@requires_redis
@pytest.mark.asyncio
async def test_task_lifecycle_success(e2e_config, e2e_token):
    """任务生命周期基本成功路径"""
    async with httpx.AsyncClient(
        base_url=e2e_config.web_api_url,
        timeout=e2e_config.http_timeout,
    ) as client:
        worker = await get_worker(client, e2e_token, e2e_config.worker_id)
        scenario = RunScenario(expected_status="success")
        async with provision_scenario(
            client,
            e2e_token,
            worker["id"],
            config=e2e_config,
            scenario=scenario,
        ) as resources:
            run = await trigger_and_wait(client, e2e_token, resources, config=e2e_config)

            assert run.get("status") == "success"
            assert run.get("exit_code", 0) == 0
            assert run.get("worker_id") == worker["id"]
