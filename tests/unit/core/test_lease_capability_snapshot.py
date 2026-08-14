from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.lease_capability_snapshot import (
    CAPABILITY_SNAPSHOT_BATCH_SIZE,
    read_live_capability_snapshots,
)

EXPECTED_KEY_COUNT = 4
EXPECTED_BATCH_CALL_COUNT = 2
FIRST_LEASE_GENERATION = 7
LAST_LEASE_GENERATION = 999


@pytest.mark.asyncio
async def test_snapshot_lua_checks_ttl_and_revocation_for_each_worker() -> None:
    redis = SimpleNamespace(
        eval=AsyncMock(
            return_value=[
                b"lease-1",
                b'{"task_types":["code"]}',
                b"7",
                b"",
                b"",
                b"",
            ]
        )
    )

    snapshots = await read_live_capability_snapshots(redis, ["worker-1", "worker-2"])

    assert snapshots["worker-1"].lease_id == "lease-1"
    assert snapshots["worker-1"].capabilities_json == '{"task_types":["code"]}'
    assert snapshots["worker-1"].lease_gen == FIRST_LEASE_GENERATION
    assert snapshots["worker-2"].lease_id == ""
    script, key_count, *arguments = redis.eval.await_args.args
    assert "SISMEMBER" in script
    assert "PTTL" in script
    assert key_count == EXPECTED_KEY_COUNT
    assert len(arguments) == EXPECTED_KEY_COUNT + 1


@pytest.mark.asyncio
async def test_snapshot_rejects_malformed_redis_response() -> None:
    redis = SimpleNamespace(eval=AsyncMock(return_value=[b"lease-only"]))

    with pytest.raises(RuntimeError, match="invalid shape"):
        await read_live_capability_snapshots(redis, ["worker-1"])


@pytest.mark.asyncio
async def test_snapshot_reads_large_worker_sets_in_bounded_batches() -> None:
    worker_ids = [f"worker-{index}" for index in range(CAPABILITY_SNAPSHOT_BATCH_SIZE + 1)]
    first_batch = [
        value for index in range(CAPABILITY_SNAPSHOT_BATCH_SIZE) for value in (f"lease-{index}", "{}", str(index + 1))
    ]
    redis = SimpleNamespace(eval=AsyncMock(side_effect=[first_batch, ["lease-last", "{}", str(LAST_LEASE_GENERATION)]]))

    snapshots = await read_live_capability_snapshots(redis, worker_ids)

    assert len(snapshots) == len(worker_ids)
    assert snapshots[worker_ids[-1]].lease_id == "lease-last"
    assert snapshots[worker_ids[-1]].lease_gen == LAST_LEASE_GENERATION
    assert redis.eval.await_count == EXPECTED_BATCH_CALL_COUNT
    first_call = redis.eval.await_args_list[0].args
    second_call = redis.eval.await_args_list[1].args
    assert first_call[1] == CAPABILITY_SNAPSHOT_BATCH_SIZE * 2
    assert second_call[1] == EXPECTED_BATCH_CALL_COUNT
