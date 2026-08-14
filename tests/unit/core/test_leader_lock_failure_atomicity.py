import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock, call

import antcode_core.infrastructure.redis.locks as locks_module
import pytest
from antcode_core.infrastructure.redis.locks import DistributedLock, FencingTokenManager

DISPATCH_TOKEN = 23


class _VerifyRedis:
    def __init__(self, result=None, error: Exception | None = None):
        self._result = result
        self._error = error

    async def eval(self, *_args):
        if self._error is not None:
            raise self._error
        return self._result


class _AcquireRedis:
    def __init__(self):
        self.set_calls = 0
        self.release_calls = 0

    async def set(self, *_args, **_kwargs):
        self.set_calls += 1
        return True

    async def eval(self, *_args):
        self.release_calls += 1
        return 1


async def _wait_for_cancelled_renew(task: asyncio.Task) -> None:
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert task.done()


@pytest.mark.asyncio
async def test_verify_error_revokes_token_and_stops_renewal():
    lock = DistributedLock("leader:test", renew_interval=60)
    lock._redis = _VerifyRedis(error=RuntimeError("redis unavailable"))
    lock._token = "term-token"
    lock._start_renew_task()
    renew_task = lock._renew_task

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await lock.verify_ownership()

    assert lock.is_locked is False
    assert lock._renew_task is None
    await _wait_for_cancelled_renew(renew_task)


@pytest.mark.asyncio
async def test_verify_mismatch_revokes_token_and_stops_renewal():
    lock = DistributedLock("leader:test", renew_interval=60)
    lock._redis = _VerifyRedis(result=0)
    lock._token = "stale-token"
    lock._start_renew_task()
    renew_task = lock._renew_task

    assert await lock.verify_ownership() is False

    assert lock.is_locked is False
    assert lock._renew_task is None
    await _wait_for_cancelled_renew(renew_task)


@pytest.mark.asyncio
async def test_fencing_failure_releases_partially_acquired_lock(monkeypatch):
    redis = _AcquireRedis()
    created_locks = []

    class _RecordingLock(DistributedLock):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created_locks.append(self)

    async def get_client():
        return redis

    monkeypatch.setattr(locks_module, "DistributedLock", _RecordingLock)
    monkeypatch.setattr(locks_module, "get_redis_client", get_client)
    monkeypatch.setattr(
        locks_module.fencing_token_manager,
        "acquire_token",
        AsyncMock(side_effect=RuntimeError("fencing incr failed")),
    )

    with pytest.raises(RuntimeError, match="fencing incr failed"):
        await locks_module.acquire_leader_lock(ttl_seconds=30)

    lock = created_locks[0]
    assert redis.set_calls == 1
    assert redis.release_calls == 1
    assert lock.is_locked is False
    assert lock._renew_task is None


@pytest.mark.asyncio
async def test_fencing_acquire_updates_dispatch_epoch_before_returning():
    redis = AsyncMock()
    redis.incr.return_value = DISPATCH_TOKEN
    manager = FencingTokenManager()
    manager._redis = redis

    assert await manager.acquire_token() == DISPATCH_TOKEN

    assert redis.method_calls[:3] == [
        call.delete(manager.DISPATCH_TOKEN_KEY),
        call.incr(manager.TOKEN_KEY),
        call.set(manager.DISPATCH_TOKEN_KEY, DISPATCH_TOKEN),
    ]
    redis.incr.assert_awaited_once_with(manager.TOKEN_KEY)
    redis.set.assert_awaited_once_with(manager.DISPATCH_TOKEN_KEY, DISPATCH_TOKEN)
    assert manager.DISPATCH_TOKEN_KEY.startswith("{antcode}:")


@pytest.mark.asyncio
async def test_fencing_mirror_remains_fail_closed_when_publish_epoch_update_fails():
    redis = AsyncMock()
    redis.incr.return_value = DISPATCH_TOKEN
    redis.set.side_effect = RuntimeError("dispatch epoch write failed")
    manager = FencingTokenManager()
    manager._redis = redis

    with pytest.raises(RuntimeError, match="dispatch epoch write failed"):
        await manager.acquire_token()

    redis.delete.assert_awaited_once_with(manager.DISPATCH_TOKEN_KEY)
    assert manager.local_token is None
