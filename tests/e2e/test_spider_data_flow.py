"""Rule crawler -> Worker -> transport -> Redis -> Web API E2E."""

import httpx
import pytest

from .conftest import requires_postgres, requires_redis
from .helpers import get_worker, trigger_task
from .run_scenarios import TERMINAL_STATUSES, wait_for_status
from .spider_data_scenario import (
    assert_expected_spider_item,
    list_spider_items,
    provision_spider_data_scenario,
)


@requires_postgres
@requires_redis
@pytest.mark.asyncio
async def test_rule_crawler_persists_queryable_spider_item(e2e_config, e2e_token) -> None:
    async with httpx.AsyncClient(
        base_url=e2e_config.web_api_url,
        timeout=e2e_config.http_timeout,
    ) as client:
        worker = await get_worker(client, e2e_token, e2e_config.worker_id)
        async with provision_spider_data_scenario(
            client,
            e2e_token,
            worker["id"],
            config=e2e_config,
        ) as resources:
            await trigger_task(client, e2e_token, resources.task["id"])
            run = await wait_for_status(
                client,
                e2e_token,
                resources.task["id"],
                expected=TERMINAL_STATUSES,
                timeout=e2e_config.poll_timeout,
                interval=e2e_config.poll_interval,
            )
            assert run["status"] == "success", run
            items = await list_spider_items(client, e2e_token, run["run_id"])
            assert_expected_spider_item(
                items,
                run_id=run["run_id"],
                project_id=resources.project["id"],
            )
