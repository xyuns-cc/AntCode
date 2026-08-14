import asyncio
from contextlib import suppress

import antcode_master.leader as leader_module
import pytest
from antcode_core.infrastructure.redis.locks import DistributedLock, LeaderLockContendedError
from antcode_master.leader import LeaderElection


class _BrokenVerifyRedis:
    async def eval(self, *_args):
        raise RuntimeError("verify connection lost")


async def _wait_for_stopped_task(task: asyncio.Task) -> None:
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert task.done()


def _active_election() -> tuple[LeaderElection, DistributedLock, asyncio.Task]:
    election = LeaderElection(ttl_seconds=300)
    lock = DistributedLock("leader:test", renew_interval=60)
    lock._redis = _BrokenVerifyRedis()
    lock._token = "term-token"
    lock._start_renew_task()
    renew_task = lock._renew_task
    election._lock = lock
    election._fencing_token = 7
    election._is_leader = True
    election._start_health_check()
    return election, lock, renew_task


@pytest.mark.asyncio
async def test_ensure_leader_verify_error_revokes_entire_term(monkeypatch):
    election, lock, renew_task = _active_election()
    health_task = election._health_check_task
    monkeypatch.setattr(leader_module, "leader_election", election)

    with pytest.raises(RuntimeError, match="verify connection lost"):
        await leader_module.ensure_leader()

    assert election.is_leader is False
    assert election.fencing_token is None
    assert election._lock is None
    assert election._health_check_task is None
    assert lock.is_locked is False
    await _wait_for_stopped_task(renew_task)
    await _wait_for_stopped_task(health_task)


@pytest.mark.asyncio
async def test_unexpected_election_failure_is_exposed(monkeypatch):
    election = LeaderElection()

    async def fail_acquire(**_kwargs):
        raise RuntimeError("fencing failed")

    monkeypatch.setattr(leader_module, "acquire_leader_lock", fail_acquire)

    with pytest.raises(RuntimeError, match="fencing failed"):
        await election.try_become_leader()


@pytest.mark.asyncio
async def test_lock_contention_remains_a_normal_follower_result(monkeypatch):
    election = LeaderElection()

    async def contend(**_kwargs):
        raise LeaderLockContendedError("held by peer")

    monkeypatch.setattr(leader_module, "acquire_leader_lock", contend)

    assert await election.try_become_leader() is False
