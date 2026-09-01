import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.scheduler.retry_queue import RetryQueueBackend


@pytest.mark.asyncio
async def test_retry_ack_exposes_missing_processing_entry():
    redis = AsyncMock()
    redis.hdel.return_value = 0
    backend = RetryQueueBackend()
    backend._get_redis = AsyncMock(return_value=redis)

    with pytest.raises(RuntimeError, match="did not remove"):
        await backend.ack("payload")


@pytest.mark.asyncio
async def test_retry_requeue_exposes_non_atomic_completion_failure():
    redis = AsyncMock()
    pipe = AsyncMock()
    pipe.zadd = MagicMock()
    pipe.hdel = MagicMock()
    pipe.execute.return_value = [1, 0]
    redis.pipeline = MagicMock(return_value=pipe)
    backend = RetryQueueBackend()
    backend._get_redis = AsyncMock(return_value=redis)

    with pytest.raises(RuntimeError, match="did not clear"):
        await backend.requeue("payload")


def _backend_with(redis) -> RetryQueueBackend:
    backend = RetryQueueBackend()
    backend._get_redis = AsyncMock(return_value=redis)
    return backend


@pytest.mark.asyncio
async def test_cancel_removes_only_the_matching_run():
    target = json.dumps({"task_id": 1, "run_id": "run-target"})
    other = json.dumps({"task_id": 1, "run_id": "run-other"})
    redis = AsyncMock()
    redis.zrange.return_value = [target, other]
    redis.zrem.return_value = 1
    backend = _backend_with(redis)

    assert await backend.cancel("run-target") == 1
    redis.zrem.assert_awaited_once_with(backend.pending_key(), target)


@pytest.mark.asyncio
async def test_cancel_is_not_wedged_by_a_corrupt_neighbouring_payload():
    """web_api 的取消端点不能因为队列里一条无关的坏 payload 就 500。

    合并后 _decode 采用 Master 的严格版(坏 JSON / 非 object 都抛),
    取消路径靠 _scan_pending 逐条跳过。b"[1, 2]" 这条在合并前会让旧的
    宽松 _decode 返回 list,随后 item.get(...) 抛 AttributeError。
    """
    target = json.dumps({"task_id": 1, "run_id": "run-target"})
    redis = AsyncMock()
    redis.zrange.return_value = [b"not-json", b"[1, 2]", target]
    redis.zrem.return_value = 1
    backend = _backend_with(redis)

    assert await backend.cancel("run-target") == 1
    redis.zrem.assert_awaited_once_with(backend.pending_key(), target)


@pytest.mark.asyncio
async def test_sweep_requeues_stale_and_unparsable_claims_only():
    """崩溃恢复只捞超时条目;claim 时间戳读不出来的按"立刻超时"处理。

    时间戳读不出来说明 processing entry 已经损坏,此时宁可重投(retry 幂等由
    durable intent 兜底)也不能让它永远悬在 processing 里不被 sweep。
    """
    now_ms = int(time.time() * 1000)
    redis = AsyncMock()
    redis.hgetall.return_value = {
        b"fresh": str(now_ms).encode(),
        b"stale": str(now_ms - 600_000).encode(),
        b"corrupt-ts": b"not-a-number",
    }
    backend = _backend_with(redis)
    backend.requeue = AsyncMock()

    expected = {"stale", "corrupt-ts"}
    assert await backend.sweep_stalled() == len(expected)
    assert {call.args[0] for call in backend.requeue.await_args_list} == expected


@pytest.mark.asyncio
async def test_cancel_skips_redis_write_when_nothing_matches():
    redis = AsyncMock()
    redis.zrange.return_value = [json.dumps({"task_id": 1, "run_id": "run-other"})]
    backend = _backend_with(redis)

    assert await backend.cancel("run-target") == 0
    redis.zrem.assert_not_awaited()
