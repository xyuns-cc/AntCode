"""
任务生命周期端到端测试

覆盖基本成功路径：创建任务 -> 调度 -> 执行 -> 结果回写
"""

import httpx
import pytest

from .conftest import requires_postgres, requires_redis
from .helpers import create_run_context, get_worker, login


@requires_postgres
@requires_redis
@pytest.mark.asyncio
async def test_task_lifecycle_success(e2e_config):
    """任务生命周期基本成功路径"""
    async with httpx.AsyncClient(
        base_url=e2e_config.web_api_url,
        timeout=e2e_config.http_timeout,
    ) as client:
        token = await login(client, e2e_config)
        worker = await get_worker(client, token, e2e_config.worker_id)
        context = await create_run_context(client, token, worker["id"], e2e_config)
        run = context["run"]

        assert run.get("status") == "success"
        assert run.get("exit_code", 0) == 0
        assert run.get("worker_id") == worker["id"]
