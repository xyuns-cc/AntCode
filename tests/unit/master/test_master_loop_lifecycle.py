import asyncio
import importlib
from dataclasses import dataclass

import pytest
from antcode_master.control.lease_sweeper_loop import LeaseSweeperLoop
from antcode_master.control.reconcile_loop import ReconcileLoop
from antcode_master.control.redispatch_loop import RedispatchLoop
from antcode_master.ingester.alert_check_loop import AlertCheckLoop
from antcode_master.ingester.artifact_cleanup_loop import ArtifactCleanupLoop
from antcode_master.ingester.crawl_batch_status_loop import CrawlBatchStatusLoop
from antcode_master.ingester.worker_registration_cleanup_loop import WorkerRegistrationCleanupLoop


@dataclass(frozen=True)
class _LoopCase:
    module_name: str
    factory: object


class _LeaseStore:
    namespace = "test"

    async def sweep_expired(self, *, batch):
        raise AssertionError(f"follower must not sweep batch={batch}")


CASES = [
    _LoopCase(
        "antcode_master.control.lease_sweeper_loop",
        lambda: LeaseSweeperLoop(_LeaseStore(), interval=60),
    ),
    _LoopCase(
        "antcode_master.control.reconcile_loop",
        lambda: ReconcileLoop(check_interval=60),
    ),
    _LoopCase(
        "antcode_master.control.redispatch_loop",
        lambda: RedispatchLoop(tick_interval_seconds=60),
    ),
    _LoopCase(
        "antcode_master.ingester.alert_check_loop",
        lambda: AlertCheckLoop(poll_interval_seconds=60),
    ),
    _LoopCase(
        "antcode_master.ingester.artifact_cleanup_loop",
        lambda: ArtifactCleanupLoop(leader_poll_interval=60),
    ),
    _LoopCase(
        "antcode_master.ingester.crawl_batch_status_loop",
        lambda: CrawlBatchStatusLoop(poll_interval_seconds=60),
    ),
    _LoopCase(
        "antcode_master.ingester.worker_registration_cleanup_loop",
        lambda: WorkerRegistrationCleanupLoop(interval_seconds=60),
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.module_name.rsplit(".", 1)[-1])
async def test_follower_loop_stops_via_cancellation(monkeypatch, case):
    checked = asyncio.Event()

    async def follower():
        checked.set()
        return False

    module = importlib.import_module(case.module_name)
    monkeypatch.setattr(module, "ensure_leader", follower)
    loop = case.factory()

    await loop.start()
    await asyncio.wait_for(checked.wait(), timeout=1)
    await loop.stop()

    assert loop._running is False
    assert loop._task is None
