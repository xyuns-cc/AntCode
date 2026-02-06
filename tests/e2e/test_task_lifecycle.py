"""
任务生命周期端到端测试

覆盖基本成功路径：创建任务 -> 调度 -> 执行 -> 结果回写
"""

import httpx
import pytest

from .conftest import requires_mysql, requires_redis
from .helpers import create_execution_context, get_worker, login


@requires_mysql
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
        context = await create_execution_context(client, token, worker["id"], e2e_config)
        execution = context["execution"]

        assert execution.get("status") == "success"
        assert execution.get("exit_code", 0) == 0
        assert execution.get("worker_id") == worker["id"]
