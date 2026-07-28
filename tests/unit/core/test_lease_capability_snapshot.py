from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.lease_capability_snapshot import read_live_capability_snapshots

EXPECTED_KEY_COUNT = 4


@pytest.mark.asyncio
async def test_snapshot_lua_checks_ttl_and_revocation_for_each_worker() -> None:
    redis = SimpleNamespace(
        eval=AsyncMock(
            return_value=[
                b"lease-1",
                b'{"task_types":["code"]}',
                b"",
                b"",
            ]
        )
    )

    snapshots = await read_live_capability_snapshots(redis, ["worker-1", "worker-2"])

    assert snapshots["worker-1"].lease_id == "lease-1"
    assert snapshots["worker-1"].capabilities_json == '{"task_types":["code"]}'
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
