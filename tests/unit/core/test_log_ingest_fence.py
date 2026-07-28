from types import SimpleNamespace

import pytest
from antcode_core.application.services.workers.log_ingest_fence import (
    LogIngestFenceRejected,
    append_fenced_log_batch,
)
from antcode_core.application.services.workers.log_ingest_fence_lua import (
    _APPEND_LOG_BATCH_SCRIPT,
)
from redis.cluster import key_slot


class _Redis:
    def __init__(self, result) -> None:
        self.result = result
        self.call = None

    async def eval(self, *args):
        self.call = args
        return self.result


@pytest.mark.asyncio
async def test_append_checks_lease_and_every_run_in_same_cluster_slot() -> None:
    redis = _Redis([1, b"10-0"])

    message_id = await append_fenced_log_batch(
        redis,
        b"proto",
        worker_id="worker-1",
        lease_id="lease-1",
        run_ids={"run-2", "run-1"},
        namespace="tenant-a",
    )

    assert message_id == "10-0"
    numkeys = redis.call[1]
    keys = redis.call[2 : 2 + numkeys]
    assert keys == (
        "{tenant-a}:lease:data:worker-1",
        "{tenant-a}:log:ingest",
        "{tenant-a}:run:owner:run-1",
        "{tenant-a}:run:owner:run-2",
    )
    assert len({key_slot(key.encode()) for key in keys}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "reason"),
    [([-1, b"lease_stale"], "lease_stale"), ([0, b"run_not_owned"], "run_not_owned")],
)
async def test_append_rejects_stale_lease_or_run_ownership(result, reason) -> None:
    redis = _Redis(result)

    with pytest.raises(LogIngestFenceRejected, match=reason):
        await append_fenced_log_batch(
            redis,
            b"proto",
            worker_id="worker-1",
            lease_id="lease-1",
            run_ids={"run-1"},
        )


def test_lua_only_appends_after_lease_and_owner_checks() -> None:
    lease_check = _APPEND_LOG_BATCH_SCRIPT.index("HGET")
    owner_check = _APPEND_LOG_BATCH_SCRIPT.index("GET', KEYS[index]")
    append = _APPEND_LOG_BATCH_SCRIPT.index("XADD")

    assert lease_check < owner_check < append
