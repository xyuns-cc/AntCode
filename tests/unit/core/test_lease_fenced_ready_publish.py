from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot
from antcode_core.application.services.lease_fenced_ready_publish import (
    LeaseDispatchFenceError,
    SchedulerDispatchFenceError,
    publish_ready_batch,
    require_scheduler_dispatch_epoch,
    scheduler_dispatch_epoch,
)
from antcode_core.application.services.workers.worker_dispatch_failure import failed_batch_dispatch
from antcode_core.application.services.workers.worker_dispatcher import BatchDispatchResult
from redis.cluster import key_slot

EXPECTED_KEY_COUNT = 4
EXPECTED_MESSAGES = 2
MIN_EXPECTED_LEASE_OCCURRENCES = 3
WORKER_SECRET = "worker-ready-publish-secret-material-0001"
# B12: publish 不再从 ContextVar 隐式继承代际，每次调用都必须显式传入。
CURRENT_EPOCH = 19


def _snapshot() -> LeaseCapabilitySnapshot:
    return LeaseCapabilitySnapshot("lease-1", '{"task_types":["code"]}', 7)


@pytest.mark.asyncio
async def test_publish_checks_lease_capabilities_and_xadds_in_one_script() -> None:
    redis = SimpleNamespace(eval=AsyncMock(return_value=[b"ok", b"1-0", b"1-1"]))

    ids = await publish_ready_batch(
        redis,
        worker_id="worker-1",
        snapshot=_snapshot(),
        messages=[{"task_id": "run-1", "priority": 0}, {"task_id": "run-2", "params": {"a": 1}}],
        scheduler_fencing_token=CURRENT_EPOCH,
        namespace="tenant",
        worker_secret=WORKER_SECRET,
    )

    assert ids == ["1-0", "1-1"]
    script, key_count, *arguments = redis.eval.await_args.args
    assert key_count == EXPECTED_KEY_COUNT
    assert arguments[:4] == [
        "{tenant}:lease:data:worker-1",
        "{tenant}:lease:revoked:worker-1",
        "{tenant}:task:ready:worker-1",
        "{tenant}:fencing:dispatch:master",
    ]
    assert len({key_slot(key.encode()) for key in arguments[:4]}) == 1
    assert "PTTL" in script
    assert "SISMEMBER" in script
    assert "capabilities_json" in script
    assert "redis.call('XADD'" in script
    assert arguments.count("dispatch_lease_id") == EXPECTED_MESSAGES
    assert arguments.count("lease-1") >= MIN_EXPECTED_LEASE_OCCURRENCES
    assert "params" not in arguments
    assert '"a":1' not in arguments


@pytest.mark.asyncio
async def test_publish_rejects_caller_supplied_conflicting_dispatch_lease() -> None:
    redis = SimpleNamespace(eval=AsyncMock())

    with pytest.raises(ValueError, match="dispatch fence"):
        await publish_ready_batch(
            redis,
            worker_id="worker-1",
            snapshot=_snapshot(),
            messages=[{"task_id": "run-1", "dispatch_lease_id": "lease-old"}],
            scheduler_fencing_token=CURRENT_EPOCH,
            worker_secret=WORKER_SECRET,
        )

    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_rejects_unencrypted_environment_alias() -> None:
    redis = SimpleNamespace(eval=AsyncMock())

    with pytest.raises(ValueError, match="unexpected sensitive fields"):
        await publish_ready_batch(
            redis,
            worker_id="worker-1",
            snapshot=_snapshot(),
            messages=[
                {
                    "task_id": "run-1",
                    "environment_vars": {"TOKEN": "must-not-reach-redis"},
                }
            ],
            scheduler_fencing_token=CURRENT_EPOCH,
            worker_secret=WORKER_SECRET,
        )

    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_checks_scheduler_epoch_in_same_xadd_script() -> None:
    redis = SimpleNamespace(eval=AsyncMock(return_value=[b"ok", b"1-0"]))

    await publish_ready_batch(
        redis,
        worker_id="worker-1",
        snapshot=_snapshot(),
        messages=[{"task_id": "run-1"}],
        scheduler_fencing_token=CURRENT_EPOCH,
        namespace="tenant",
        worker_secret=WORKER_SECRET,
    )

    script, _key_count, *arguments = redis.eval.await_args.args
    assert arguments[3] == "{tenant}:fencing:dispatch:master"
    assert arguments[8] == str(CURRENT_EPOCH)
    assert "redis.call('GET', KEYS[4])" in script


@pytest.mark.asyncio
async def test_scheduler_epoch_rejection_has_distinct_exception() -> None:
    redis = SimpleNamespace(eval=AsyncMock(return_value=[b"scheduler_generation_changed"]))

    with pytest.raises(SchedulerDispatchFenceError):
        await publish_ready_batch(
            redis,
            worker_id="worker-1",
            snapshot=_snapshot(),
            messages=[{"task_id": "run-1"}],
            scheduler_fencing_token=CURRENT_EPOCH,
            worker_secret=WORKER_SECRET,
        )


@pytest.mark.asyncio
async def test_publish_forwards_the_epoch_read_from_the_dispatch_context() -> None:
    """B12: 代际不再由 publish 隐式继承，调用方必须显式取值后传入。

    ``scheduler_dispatch_epoch()`` 只负责绑定，``require_scheduler_dispatch_epoch()``
    是唯一取值入口；两者之间的传递必须是显式实参。
    """
    redis = SimpleNamespace(eval=AsyncMock(return_value=[b"ok", b"1-0"]))

    with scheduler_dispatch_epoch(CURRENT_EPOCH):
        await publish_ready_batch(
            redis,
            worker_id="worker-1",
            snapshot=_snapshot(),
            messages=[{"task_id": "run-1"}],
            scheduler_fencing_token=require_scheduler_dispatch_epoch(),
            worker_secret=WORKER_SECRET,
        )

    assert redis.eval.await_args.args[10] == str(CURRENT_EPOCH)


@pytest.mark.asyncio
async def test_explicit_zero_scheduler_epoch_is_rejected_instead_of_inherited() -> None:
    redis = SimpleNamespace(eval=AsyncMock())

    with scheduler_dispatch_epoch(CURRENT_EPOCH), pytest.raises(ValueError, match="positive"):
        await publish_ready_batch(
            redis,
            worker_id="worker-1",
            snapshot=_snapshot(),
            messages=[{"task_id": "run-1"}],
            scheduler_fencing_token=0,
            worker_secret=WORKER_SECRET,
        )

    redis.eval.assert_not_awaited()


def test_batch_dispatch_failure_does_not_swallow_scheduler_fence() -> None:
    error = SchedulerDispatchFenceError("stale")

    with pytest.raises(SchedulerDispatchFenceError) as raised:
        failed_batch_dispatch(SimpleNamespace(name="worker-1"), error, BatchDispatchResult)

    assert raised.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        "lease_changed",
        "lease_expired",
        "lease_revoked",
        "capabilities_changed",
        "lease_generation_changed",
        "scheduler_generation_changed",
    ],
)
async def test_publish_rejects_every_fence_failure(outcome: str) -> None:
    redis = SimpleNamespace(eval=AsyncMock(return_value=[outcome.encode()]))

    with pytest.raises(LeaseDispatchFenceError, match=outcome):
        await publish_ready_batch(
            redis,
            worker_id="worker-1",
            snapshot=_snapshot(),
            messages=[{"task_id": "run-1"}],
            scheduler_fencing_token=CURRENT_EPOCH,
            worker_secret=WORKER_SECRET,
        )
