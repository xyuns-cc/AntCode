"""P1-round6 5.2 回归:_on_batch_cancelled 循环收敛防陈旧快照。

审查文档 round6 5.2:
`batch/stop/crawl cancel 仍根据陈旧快照决定是否发送 control; 快照后新建
或新绑定 run 可继续执行`。

Bug 场景:
- T0: _on_batch_cancelled 拿快照 = [r1, r2]
- T1: _on_batch_started 已入队但未收敛,并发在 T0 之后建 r3
- T2: 只对 r1, r2 发 cancel; r3 未包含 → 继续执行 → 用户看到 batch cancelled
  但 r3 还在跑

修复:改为 loop-until-empty (max 5 轮),每轮拿最新 active runs 减去已处理
集合;上游 batch.status 已 CANCELLED, 后续 _on_batch_started 被 L76 拦住,
循环必然收敛。

本测试锁死:
1. 单轮空 → no-op
2. 单轮全部命中 → 一轮结束
3. 后续轮次新增 run → 继续 cancel
4. 多轮收敛后不重复处理已 cancel 的 run
5. max_rounds 硬止损防死循环
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.application.services.crawl.batch_dispatcher_service import (
    CrawlBatchDispatcherService,
)

_EXPECTED_TWO_RUNS = 2
_EXPECTED_THREE_RUNS = 3
_MAX_ROUNDS = 5


@pytest.mark.asyncio
async def test_no_active_runs_noop():
    svc = CrawlBatchDispatcherService()
    with (
        patch.object(svc, "_active_run_ids_for_batch", AsyncMock(return_value=[])) as m_query,
        patch.object(svc, "_cancel_active_run", AsyncMock()) as m_cancel,
    ):
        await svc._on_batch_cancelled("b-1")
    m_query.assert_awaited()  # 至少调用一次
    m_cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_round_cancels_all():
    svc = CrawlBatchDispatcherService()
    # 第一次查有 2 run, 第二次查空
    query = AsyncMock(side_effect=[["r-1", "r-2"], []])
    cancel = AsyncMock(return_value=(True, True))
    with patch.object(svc, "_active_run_ids_for_batch", query), patch.object(svc, "_cancel_active_run", cancel):
        await svc._on_batch_cancelled("b-1")
    assert cancel.await_count == _EXPECTED_TWO_RUNS
    calls = {c.args[0] for c in cancel.await_args_list}
    assert calls == {"r-1", "r-2"}


@pytest.mark.asyncio
async def test_second_round_catches_newly_added_run():
    """P1-round6 5.2 关键: 第一次快照后新建的 run, 第二轮必须捕获。"""
    svc = CrawlBatchDispatcherService()
    # 第一轮 [r-1, r-2]; 第二轮 [r-1, r-2, r-3] (r-3 是快照后新建); 第三轮空
    query = AsyncMock(side_effect=[["r-1", "r-2"], ["r-1", "r-2", "r-3"], []])
    cancel = AsyncMock(return_value=(True, True))
    with patch.object(svc, "_active_run_ids_for_batch", query), patch.object(svc, "_cancel_active_run", cancel):
        await svc._on_batch_cancelled("b-1")
    # r-1, r-2, r-3 各一次, r-1 r-2 不重复
    assert cancel.await_count == _EXPECTED_THREE_RUNS
    ran = [c.args[0] for c in cancel.await_args_list]
    assert ran == ["r-1", "r-2", "r-3"]


@pytest.mark.asyncio
async def test_already_seen_runs_not_re_cancelled():
    """已 cancel 的 run 在下一轮 active 中再出现时不重复处理。"""
    svc = CrawlBatchDispatcherService()
    # 每轮返回相同的 [r-1, r-2] (模拟 DB 还未落终态可见)
    query = AsyncMock(side_effect=[["r-1", "r-2"], ["r-1", "r-2"], []])
    cancel = AsyncMock(return_value=(True, True))
    with patch.object(svc, "_active_run_ids_for_batch", query), patch.object(svc, "_cancel_active_run", cancel):
        await svc._on_batch_cancelled("b-1")
    # 只对每个 run 调一次
    assert cancel.await_count == _EXPECTED_TWO_RUNS


@pytest.mark.asyncio
async def test_max_rounds_hard_stop():
    """病态: DB 每轮都返回新 run (上游未收敛) 时, 硬止损防死循环。"""
    svc = CrawlBatchDispatcherService()
    # 每轮无穷返回新 run
    counter = {"i": 0}

    async def endless_query(_batch_id: str):
        counter["i"] += 1
        return [f"r-{counter['i']}"]

    cancel = AsyncMock(return_value=(True, True))
    with patch.object(svc, "_active_run_ids_for_batch", endless_query), patch.object(svc, "_cancel_active_run", cancel):
        await svc._on_batch_cancelled("b-1")
    # max_rounds = 5, 每轮 cancel 一个新 run
    assert cancel.await_count == _MAX_ROUNDS


@pytest.mark.asyncio
async def test_cancel_failure_raises_after_loop():
    """有 cancel 失败时仍循环完再 raise, 不提前退出。"""
    svc = CrawlBatchDispatcherService()
    query = AsyncMock(side_effect=[["r-fail", "r-2"], []])
    # r-fail 失败, r-2 成功
    cancel = AsyncMock(side_effect=[(False, False), (True, True)])
    with patch.object(svc, "_active_run_ids_for_batch", query), patch.object(svc, "_cancel_active_run", cancel):
        with pytest.raises(RuntimeError, match="batch 取消未完成"):
            await svc._on_batch_cancelled("b-1")
    assert cancel.await_count == _EXPECTED_TWO_RUNS
