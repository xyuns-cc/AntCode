import httpx
import pytest

from .conftest import requires_postgres, requires_redis
from .helpers import extract_data, get_worker, request_json, trigger_task
from .run_scenarios import (
    RunScenario,
    provision_scenario,
    wait_for_run_count,
    wait_for_status,
)


@requires_postgres
@requires_redis
@pytest.mark.asyncio
async def test_running_task_can_be_cancelled(e2e_config, e2e_token) -> None:
    scenario = RunScenario(expected_status="cancelled", script_delay_seconds=60)
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
            task_id = resources.task["id"]
            await trigger_task(client, e2e_token, task_id)
            running = await wait_for_status(
                client,
                e2e_token,
                task_id,
                expected=frozenset({"running"}),
                timeout=e2e_config.poll_timeout,
                interval=e2e_config.poll_interval,
            )
            payload = await request_json(
                client,
                "POST",
                f"/runs/{running['run_id']}/cancel",
                token=e2e_token,
            )
            assert (extract_data(payload) or {})["remote_cancelled"] is True
            cancelled = await wait_for_status(
                client,
                e2e_token,
                task_id,
                expected=frozenset({"cancelled"}),
                timeout=e2e_config.poll_timeout,
                interval=e2e_config.poll_interval,
            )
            assert cancelled["run_id"] == running["run_id"]


@requires_postgres
@requires_redis
@pytest.mark.asyncio
async def test_failed_task_retries_once(e2e_config, e2e_token) -> None:
    scenario = RunScenario(expected_status="failed", exit_code=9, retry_count=1)
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
            task_id = resources.task["id"]
            await trigger_task(client, e2e_token, task_id)
            runs = await wait_for_run_count(
                client,
                e2e_token,
                task_id,
                minimum=2,
                timeout=e2e_config.poll_timeout,
                interval=e2e_config.poll_interval,
            )

            assert {run["status"] for run in runs[:2]} == {"failed"}
            assert len({run["run_id"] for run in runs[:2]}) == 2
            assert any("retry_source_run_id" in (run.get("result_data") or {}) for run in runs[:2])
