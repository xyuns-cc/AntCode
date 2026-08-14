"""Redis 重启后 EVALSHA 必须自愈——回归 P0：按子串判断 NOSCRIPT 的死代码。

Redis 的脚本缓存是易失的：重启、``SCRIPT FLUSH``、副本切换后 SHA 一律失效，
服务端回 ``NOSCRIPT No matching script. Please use EVAL.``。redis-py 把它映射成
``NoScriptError`` 时**已经剥掉了错误码前缀**，``str(exc)`` 只剩
"No matching script. Please use EVAL."，因此 ``if "NOSCRIPT" in str(exc)`` 恒为假，
整条回退路径是死代码。真机后果：测试机上重建 Redis 容器后，Gateway 的每一次
Lease 签发都以 ``NoScriptError`` 失败，Worker 全部掉线且**永不恢复**，直到控制面
进程重启。这里对每个 EVALSHA 调用点断言：抛真实 ``NoScriptError`` 时必须回退 EVAL。
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.lease_service import LeaseStore
from antcode_core.application.services.scheduler.redispatch_service import RedispatchService
from redis.exceptions import NoScriptError

NAMESPACE = "antcode-test"
LEASE_SCRIPT_COUNT = 5
CONSECUTIVE_CALLS = 2


def _redis_with_dead_script_cache(eval_result: Any) -> AsyncMock:
    redis = AsyncMock()
    redis.script_load.return_value = "stale-sha"
    redis.evalsha.side_effect = NoScriptError("No matching script. Please use EVAL.")
    redis.eval.return_value = eval_result
    return redis


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        ("_evalsha_grant", ([], [])),
        ("_evalsha_revoke", ([], [])),
        ("_evalsha_disable_worker", ([], [])),
        ("_evalsha_enable_worker", ([], [])),
        ("_evalsha_sweep_delete", ([], [])),
    ],
)
@pytest.mark.asyncio
async def test_lease_store_reloads_every_script_after_redis_restart(method, arguments):
    redis = _redis_with_dead_script_cache(["ok"])
    store = LeaseStore(redis, namespace=NAMESPACE)

    assert await getattr(store, method)(*arguments) == ["ok"]

    redis.eval.assert_awaited_once()
    # SHA 缓存必须被清掉，否则下一次调用还会拿着失效 SHA 打 EVALSHA。
    assert not store._scripts_loaded.is_set()


@pytest.mark.asyncio
async def test_lease_grant_recovers_across_consecutive_calls():
    redis = _redis_with_dead_script_cache(["ok"])
    store = LeaseStore(redis, namespace=NAMESPACE)

    for _ in range(CONSECUTIVE_CALLS):
        assert await store._evalsha_grant([], []) == ["ok"]

    assert redis.eval.await_count == CONSECUTIVE_CALLS
    # 第二轮只补装被作废的那一支：其余 SHA 仍在缓存里，各自首次命中 NOSCRIPT 时自愈。
    assert redis.script_load.await_count == LEASE_SCRIPT_COUNT + 1


@pytest.mark.asyncio
async def test_redispatch_claim_falls_back_to_eval_after_redis_restart(monkeypatch):
    redis = _redis_with_dead_script_cache([])
    monkeypatch.setattr(
        "antcode_core.application.services.scheduler.redispatch_service.get_redis_client",
        AsyncMock(return_value=redis),
    )
    queue = RedispatchService()

    assert await queue.claim_due(limit=1) == []

    redis.eval.assert_awaited_once()
    assert queue._claim_sha is None


@pytest.mark.asyncio
async def test_lease_store_still_propagates_unrelated_redis_errors():
    redis = AsyncMock()
    redis.script_load.return_value = "sha"
    redis.evalsha.side_effect = ConnectionError("redis unavailable")
    store = LeaseStore(redis, namespace=NAMESPACE)

    with pytest.raises(ConnectionError, match="redis unavailable"):
        await store._evalsha_grant([], [])
    redis.eval.assert_not_awaited()


def test_noscript_error_message_never_contains_the_error_code():
    """锁死本次缺陷的根因：按子串判断 NOSCRIPT 永远不成立。"""
    assert "NOSCRIPT" not in str(NoScriptError("No matching script. Please use EVAL."))
