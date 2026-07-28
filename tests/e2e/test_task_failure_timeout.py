import httpx
import pytest

from .conftest import requires_postgres, requires_redis
from .helpers import get_worker
from .run_scenarios import RunScenario, provision_scenario, trigger_and_wait


@requires_postgres
@requires_redis
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(RunScenario(expected_status="failed", exit_code=7), id="failed-exit"),
        pytest.param(
            RunScenario(expected_status="timeout", script_delay_seconds=30, timeout_seconds=2),
            id="timeout",
        ),
    ],
)
async def test_task_failure_and_timeout(e2e_config, e2e_token, scenario) -> None:
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
            scenario=scenario,
        ) as resources:
            run = await trigger_and_wait(client, e2e_token, resources, config=e2e_config)

            assert run["status"] == scenario.expected_status
            if scenario.expected_status == "failed":
                assert run["exit_code"] == scenario.exit_code
