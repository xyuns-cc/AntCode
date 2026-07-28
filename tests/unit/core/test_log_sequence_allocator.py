from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_core.application.services.logs.log_sequence_allocator as allocator_module
import pytest
from antcode_core.application.services.logs.log_sequence_allocator import RedisLogSequenceAllocator


@pytest.mark.asyncio
async def test_hot_path_skips_postgres_floor_when_redis_key_exists(monkeypatch):
    redis = SimpleNamespace(eval=AsyncMock(return_value=8))
    max_sequence = AsyncMock(return_value=5)
    monkeypatch.setattr(allocator_module.postgres_task_log_service, "max_sequence", max_sequence)
    monkeypatch.setattr(allocator_module, "get_redis_client", AsyncMock(return_value=redis))

    sequences = await RedisLogSequenceAllocator().allocate("run-1", "stdout", 3)

    assert sequences == [6, 7, 8]
    max_sequence.assert_not_awaited()
    redis.eval.assert_awaited_once()
    args = redis.eval.await_args.args
    assert args[1] == 1
    assert args[2].endswith(":log:sequence:run-1:stdout")
    assert args[3:5] == (3, allocator_module.SEQUENCE_KEY_TTL_SECONDS)


@pytest.mark.asyncio
async def test_missing_key_seeds_from_postgres_floor_with_atomic_script(monkeypatch):
    redis = SimpleNamespace(eval=AsyncMock(side_effect=[allocator_module._MISSING_KEY_SENTINEL, 8]))
    monkeypatch.setattr(
        allocator_module.postgres_task_log_service,
        "max_sequence",
        AsyncMock(return_value=5),
    )
    monkeypatch.setattr(allocator_module, "get_redis_client", AsyncMock(return_value=redis))

    sequences = await RedisLogSequenceAllocator().allocate("run-1", "stdout", 3)

    assert sequences == [6, 7, 8]
    assert redis.eval.await_count == 2
    seed_args = redis.eval.await_args_list[1].args
    assert seed_args[0] == allocator_module._SEED_ALLOCATE_LUA
    assert seed_args[1] == 1
    assert seed_args[2].endswith(":log:sequence:run-1:stdout")
    assert seed_args[3:6] == (5, 3, allocator_module.SEQUENCE_KEY_TTL_SECONDS)


@pytest.mark.asyncio
async def test_sequence_floor_failure_is_not_silently_reset(monkeypatch):
    redis = SimpleNamespace(eval=AsyncMock(return_value=allocator_module._MISSING_KEY_SENTINEL))
    monkeypatch.setattr(
        allocator_module.postgres_task_log_service,
        "max_sequence",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    monkeypatch.setattr(allocator_module, "get_redis_client", AsyncMock(return_value=redis))

    with pytest.raises(RuntimeError, match="db down"):
        await RedisLogSequenceAllocator().allocate("run-1", "stdout", 1)

    # 播种失败时绝不带着未播种的 floor 继续分配
    redis.eval.assert_awaited_once()
