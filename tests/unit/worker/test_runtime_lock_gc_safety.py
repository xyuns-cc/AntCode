"""Runtime 锁和 GC 并发安全回归测试。"""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from antcode_worker.runtime.gc import GCPolicy, RuntimeGC
from antcode_worker.runtime.hash import compute_runtime_hash
from antcode_worker.runtime.locks import FileLock, RuntimeLock
from antcode_worker.runtime.manager import RuntimeManager, RuntimeManagerConfig
from antcode_worker.runtime.spec import RuntimeSpec

LOCK_OBSERVATION_ATTEMPTS = 20
LOCK_OBSERVATION_DELAY_SECONDS = 0.001
NO_WAIT_ASSERTION_TIMEOUT_SECONDS = 0.1
LOCK_USERS_WITH_ONE_WAITER = 2


def _create_runtime(root: Path, name: str, manifest: dict[str, str]) -> Path:
    runtime_path = root / name
    python_path = runtime_path / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.touch()
    (runtime_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return runtime_path


def _expired_timestamp() -> str:
    return (datetime.now() - timedelta(days=1)).isoformat()


async def _wait_for_waiter(lock: RuntimeLock, runtime_hash: str) -> None:
    for _ in range(LOCK_OBSERVATION_ATTEMPTS):
        if lock._lock_users.get(runtime_hash) == LOCK_USERS_WITH_ONE_WAITER:
            return
        await asyncio.sleep(LOCK_OBSERVATION_DELAY_SECONDS)
    raise AssertionError("删除协程没有进入运行时锁等待队列")


@pytest.mark.asyncio
async def test_runtime_lock_timeout_zero_does_not_use_default_timeout() -> None:
    lock = RuntimeLock(default_timeout=60)
    assert await lock.acquire("runtime") is True

    try:
        acquired = await asyncio.wait_for(
            lock.acquire("runtime", timeout=0),
            timeout=NO_WAIT_ASSERTION_TIMEOUT_SECONDS,
        )
        assert acquired is False
    finally:
        await lock.release("runtime")


@pytest.mark.asyncio
async def test_expired_lock_keeps_accurate_state_until_holder_releases() -> None:
    lock = RuntimeLock(default_timeout=0)
    assert await lock.acquire("runtime", timeout=0) is True

    await lock._cleanup_expired_locks()
    await lock._cleanup_expired_locks()

    assert lock.is_locked("runtime") is True
    assert lock.get_lock_info("runtime").timed_out is True
    assert lock.get_stats().current_held == 1
    assert lock.get_stats().total_timeouts == 1
    assert await lock.release("runtime") is True
    assert lock.get_stats().current_held == 0


@pytest.mark.asyncio
async def test_file_lock_timeout_zero_attempts_once_without_waiting(tmp_path: Path) -> None:
    lock = FileLock(str(tmp_path), default_timeout=60)
    assert await lock.acquire("held", timeout=0) is True

    try:
        assert await lock.acquire("held", timeout=0) is False
        assert await lock.acquire("free", timeout=0) is True
        assert await lock.release("free") is True
    finally:
        await lock.release("held")


@pytest.mark.asyncio
async def test_prepare_wait_for_lock_false_never_enters_wait_queue(tmp_path: Path) -> None:
    manager = RuntimeManager(RuntimeManagerConfig(venvs_dir=str(tmp_path), auto_gc=False))
    spec = RuntimeSpec()
    runtime_hash = compute_runtime_hash(spec)
    manager._builder.build = AsyncMock()
    assert await manager._lock.acquire(runtime_hash) is True

    try:
        with pytest.raises(RuntimeError, match="无法获取运行时锁"):
            await asyncio.wait_for(
                manager.prepare(spec, wait_for_lock=False),
                timeout=NO_WAIT_ASSERTION_TIMEOUT_SECONDS,
            )
        manager._builder.build.assert_not_awaited()
    finally:
        await manager._lock.release(runtime_hash)


@pytest.mark.asyncio
async def test_remove_rechecks_usage_after_acquiring_runtime_lock(tmp_path: Path) -> None:
    manager = RuntimeManager(RuntimeManagerConfig(venvs_dir=str(tmp_path), auto_gc=False))
    runtime_hash = "runtime"
    manager._builder.remove = AsyncMock(return_value=True)
    assert await manager._lock.acquire(runtime_hash) is True

    removal = asyncio.create_task(manager.remove(runtime_hash))
    await _wait_for_waiter(manager._lock, runtime_hash)
    manager._usage_count[runtime_hash] = 1
    await manager._lock.release(runtime_hash)

    assert await removal is False
    manager._builder.remove.assert_not_awaited()


@pytest.mark.asyncio
async def test_gc_skips_runtime_while_build_lock_is_held(tmp_path: Path) -> None:
    policy = GCPolicy(ttl_seconds=1, max_count=0, disk_high_watermark=2, auto_gc=False)
    manager = RuntimeManager(RuntimeManagerConfig(venvs_dir=str(tmp_path), gc_policy=policy))
    runtime_hash = "managed-runtime"
    runtime_path = _create_runtime(
        tmp_path,
        runtime_hash,
        {"runtime_hash": runtime_hash, "last_used": _expired_timestamp()},
    )
    assert await manager._lock.acquire(runtime_hash) is True

    try:
        result = await manager.run_gc()
    finally:
        await manager._lock.release(runtime_hash)

    assert result["cleaned"] == 0
    assert runtime_path.exists()


@pytest.mark.asyncio
async def test_manager_gc_deletes_idle_managed_runtime(tmp_path: Path) -> None:
    policy = GCPolicy(ttl_seconds=1, max_count=0, disk_high_watermark=2, auto_gc=False)
    manager = RuntimeManager(RuntimeManagerConfig(venvs_dir=str(tmp_path), gc_policy=policy))
    runtime_hash = "idle-runtime"
    runtime_path = _create_runtime(
        tmp_path,
        runtime_hash,
        {"runtime_hash": runtime_hash, "last_used": _expired_timestamp()},
    )

    result = await manager.run_gc()

    assert result["cleaned"] == 1
    assert not runtime_path.exists()


@pytest.mark.asyncio
async def test_gc_never_collects_uv_manager_named_environment(tmp_path: Path) -> None:
    env_name = "shared-production"
    env_path = _create_runtime(
        tmp_path,
        env_name,
        {"name": env_name, "last_used": _expired_timestamp()},
    )
    policy = GCPolicy(ttl_seconds=1, max_count=0, disk_high_watermark=2, auto_gc=False)

    result = await RuntimeGC(str(tmp_path), policy).run_gc()

    assert result["cleaned"] == 0
    assert env_path.exists()
