"""Guarded Worker list, heartbeat, and externally orchestrated churn scenarios."""

from __future__ import annotations

import pytest

from tests.loadtest.tool.api import AntCodeApi
from tests.loadtest.tool.config import LoadSettings
from tests.loadtest.tool.metrics import assert_report, emit_report
from tests.loadtest.tool.runner import run_load
from tests.loadtest.tool.scenarios import assert_churn, worker_list_value, worker_state

pytestmark = [pytest.mark.loadtest_scenario, pytest.mark.asyncio]
HTTP_OK = frozenset({200})


@pytest.mark.asyncio
async def test_many_workers_connection(load_settings: LoadSettings) -> None:
    """Read the complete Worker page concurrently and require stable inventory."""
    async with AntCodeApi(load_settings) as api:

        async def list_workers(index: int):
            return await api.call("GET", "/workers?page=1&size=100", index)

        report = await run_load("worker-inventory", load_settings.stage, list_workers)
    assert_report(report, load_settings.thresholds, HTTP_OK)
    inventories = tuple(worker_list_value(value) for value in report.values)
    totals = {total for total, _ in inventories}
    pages = {ids for _, ids in inventories}
    assert totals and min(totals) >= load_settings.min_workers
    assert pages and min(len(ids) for ids in pages) >= load_settings.min_workers
    assert len(totals) == 1
    assert len(pages) == 1
    emit_report(report)


@pytest.mark.asyncio
async def test_worker_heartbeat_scalability(load_settings: LoadSettings) -> None:
    """Poll one real Worker and require its heartbeat timestamp to advance."""
    worker_id = _require_worker_id(load_settings)
    async with AntCodeApi(load_settings) as api:

        async def read_worker(index: int):
            return await api.call("GET", f"/workers/{worker_id}", index)

        report = await run_load("worker-heartbeat", load_settings.stage, read_worker)
    assert_report(report, load_settings.thresholds, HTTP_OK)
    states = tuple(worker_state(value) for value in report.values)
    heartbeats = {heartbeat for _, _, heartbeat in states if heartbeat}
    assert all(current_id == worker_id for current_id, _, _ in states)
    assert all(status.lower() == "online" for _, status, _ in states)
    assert len(heartbeats) >= 2, f"heartbeat did not advance: {sorted(heartbeats)}"
    emit_report(report)


@pytest.mark.asyncio
async def test_worker_churn(load_settings: LoadSettings) -> None:
    """Observe real externally restarted Workers transition online/offline/online."""
    worker_ids = load_settings.churn_worker_ids
    if not worker_ids:
        raise ValueError("churn scenario requires ANTCODE_LOADTEST_CHURN_WORKER_IDS")
    async with AntCodeApi(load_settings) as api:

        async def read_worker(index: int):
            worker_id = worker_ids[index % len(worker_ids)]
            return await api.call("GET", f"/workers/{worker_id}", index)

        report = await run_load("worker-churn", load_settings.stage, read_worker)
    assert_report(report, load_settings.thresholds, HTTP_OK)
    states = tuple(worker_state(value) for value in report.values)
    assert_churn(states, worker_ids)
    emit_report(report)


def _require_worker_id(settings: LoadSettings) -> str:
    if not settings.worker_id:
        raise ValueError("heartbeat scenario requires ANTCODE_LOADTEST_WORKER_ID")
    return settings.worker_id
