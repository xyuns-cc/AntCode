import httpx
import pytest

from .conftest import requires_postgres, requires_redis
from .helpers import API_PREFIX, get_worker, trigger_task
from .run_scenarios import RunScenario, list_task_runs, provision_scenario, wait_for_status


@requires_postgres
@requires_redis
@pytest.mark.asyncio
async def test_immediate_duplicate_trigger_is_rejected(e2e_config, e2e_token) -> None:
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
            scenario=RunScenario(expected_status="success", script_delay_seconds=1),
        ) as resources:
            task_id = resources.task["id"]
            await trigger_task(client, e2e_token, task_id)
            duplicate = await client.post(
                f"{API_PREFIX}/tasks/{task_id}/trigger",
                headers={"Authorization": f"Bearer {e2e_token}"},
            )

            assert duplicate.status_code == 409
            await wait_for_status(
                client,
                e2e_token,
                task_id,
                expected=frozenset({"success"}),
                timeout=e2e_config.poll_timeout,
                interval=e2e_config.poll_interval,
            )
            assert len(await list_task_runs(client, e2e_token, task_id)) == 1
