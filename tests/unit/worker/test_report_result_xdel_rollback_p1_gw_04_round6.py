"""P1-GW-04 (round6) 回归:report_result XADD 后代际切换 → XDEL 撤销 orphan。

审查文档 round6 5.1:
`Lease check 与 XADD/EVAL/XACK 分离仍有 TOCTOU; 切代可插入两步之间,旧
Worker 继续提交或 ACK`。

Direct 传输 XADD 前 `_require_current_generation` 已 check(前置),XADD
后 `_require_current_generation` 又 check(后置)。原实现后置发现代际
已切时只 raise GenerationLostError → return False, 但 XADD 已经落到
result stream, 靠 master 侧 lease_validator 拒收。这在 defense-in-depth
下不会造成"错误状态覆盖", 但 result stream 会堆积 orphan 消息。

修复(round6): 后置 check False 时, 主动 XDEL 掉刚写入的 msg_id, 让
orphan 不进入 stream; XDEL 失败仅 warn (master 侧 fence 已够), 不阻塞。

本测试锁死:
1. XADD 前 lease 有效 → xadd 成功
2. XADD 后 lease 无效 → xdel(result_key, msg_id) 被调用
3. return False (engine 不 XACK, 消息在源 ready-stream 保留待 reclaim)
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.transport.base import TaskResult
from antcode_worker.transport.redis.transport import RedisTransport


@pytest.mark.asyncio
async def test_report_result_xdel_orphan_on_post_check_lease_lost():
    """P1-GW-04: XADD 后代际切换 → 主动 XDEL orphan 消息 + return False。"""
    transport = RedisTransport.__new__(RedisTransport)
    transport._redis = MagicMock()
    transport._running = True
    transport._worker_id = "w-1"
    transport._lease_id = "lease-A"
    transport._lease_fencing_enabled = True
    transport._receipt_cache = {}

    # keys
    transport._keys = MagicMock()
    transport._keys.task_result_stream = MagicMock(return_value="antcode:task:result")

    # lease store: 前置 check True, 后置 check False
    lease_store = MagicMock()
    lease_store.is_current = AsyncMock(side_effect=[True, False])
    transport._lease_store = lease_store

    # xadd 返回 msg_id, xdel 挂 spy
    transport._redis.xadd = AsyncMock(return_value=b"1730000000000-0")
    transport._redis.xdel = AsyncMock(return_value=1)

    # _run_with_reconnect passthrough
    async def _passthrough(_msg, coro_factory):
        return await coro_factory()

    transport._run_with_reconnect = _passthrough  # type: ignore[assignment]

    result = TaskResult(
        run_id="r-1",
        task_id="t-1",
        status="success",
        exit_code=0,
        error_message="",
        started_at=datetime(2026, 7, 23),
        finished_at=datetime(2026, 7, 23),
        duration_ms=100,
        data={},
    )

    ok = await transport.report_result(result)

    assert ok is False, "post-check 失败必须返回 False, 让 engine 保留 receipt"
    transport._redis.xadd.assert_awaited_once()
    # 关键: xdel 被调用清 orphan
    transport._redis.xdel.assert_awaited_once_with("antcode:task:result", b"1730000000000-0")


@pytest.mark.asyncio
async def test_report_result_xdel_failure_does_not_mask_generation_lost():
    """P1-GW-04: XDEL 失败(cluster 跨 slot / 连接抖动) 仍 return False, 不阻塞。"""
    transport = RedisTransport.__new__(RedisTransport)
    transport._redis = MagicMock()
    transport._running = True
    transport._worker_id = "w-1"
    transport._lease_id = "lease-A"
    transport._lease_fencing_enabled = True
    transport._receipt_cache = {}

    transport._keys = MagicMock()
    transport._keys.task_result_stream = MagicMock(return_value="antcode:task:result")

    lease_store = MagicMock()
    lease_store.is_current = AsyncMock(side_effect=[True, False])
    transport._lease_store = lease_store

    transport._redis.xadd = AsyncMock(return_value=b"1730000000000-0")
    # XDEL raise 不应阻塞 return False 路径
    transport._redis.xdel = AsyncMock(side_effect=RuntimeError("cluster CROSSSLOT"))

    async def _passthrough(_msg, coro_factory):
        return await coro_factory()

    transport._run_with_reconnect = _passthrough  # type: ignore[assignment]

    result = TaskResult(
        run_id="r-1",
        task_id="t-1",
        status="success",
        exit_code=0,
        error_message="",
        started_at=datetime(2026, 7, 23),
        finished_at=datetime(2026, 7, 23),
        duration_ms=100,
        data={},
    )

    ok = await transport.report_result(result)

    assert ok is False
    # XDEL 尝试过
    transport._redis.xdel.assert_awaited_once()


@pytest.mark.asyncio
async def test_report_result_no_xdel_when_generation_stable():
    """P1-GW-04 反面: 代际稳定 → 不调用 XDEL, 正常 return True。"""
    transport = RedisTransport.__new__(RedisTransport)
    transport._redis = MagicMock()
    transport._running = True
    transport._worker_id = "w-1"
    transport._lease_id = "lease-A"
    transport._lease_fencing_enabled = True
    transport._receipt_cache = {}

    transport._keys = MagicMock()
    transport._keys.task_result_stream = MagicMock(return_value="antcode:task:result")

    lease_store = MagicMock()
    lease_store.is_current = AsyncMock(return_value=True)
    transport._lease_store = lease_store

    transport._redis.xadd = AsyncMock(return_value=b"1730000000000-0")
    transport._redis.xdel = AsyncMock()

    async def _passthrough(_msg, coro_factory):
        return await coro_factory()

    transport._run_with_reconnect = _passthrough  # type: ignore[assignment]

    result = TaskResult(
        run_id="r-1",
        task_id="t-1",
        status="success",
        exit_code=0,
        error_message="",
        started_at=datetime(2026, 7, 23),
        finished_at=datetime(2026, 7, 23),
        duration_ms=100,
        data={},
    )

    ok = await transport.report_result(result)

    assert ok is True
    transport._redis.xdel.assert_not_called()
