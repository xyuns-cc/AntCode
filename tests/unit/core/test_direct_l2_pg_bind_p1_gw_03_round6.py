"""P1-GW-03 (round6) 回归:Direct L2 takeover 后 PG lease bind L1 → L2 换代。

审查文档 round6 5.1 P1-GW-03:
`Direct L2 claim 未接线 PG Lease generation bind → 副作用已发生但状态/日志
被旧 PG 绑定拒绝`。

## Bug 场景

- Direct Worker L1 执行 run-A,PG.lease_id = L1
- L1 崩溃,Master reconcile 判死
- Direct Worker L2 XAUTOCLAIM 拿到 run-A 的 PEL entry
- L2 fence Redis ownership: ACQUIRED
- L2 执行,XADD result stream, TaskStatus.data.lease_id = L2
- Master result_loop 消费, task_run_service.update_result → _validate_result_lease:
  * lease_validator(L2) = True (L2 是 current)
  * _bind_lease_generation CAS `lease_id IS NULL OR lease_id = L2`
  * 但 PG.lease_id = L1, CAS 匹配失败 → 返回 False → update_result 拒
- L2 已实际执行 run,但状态/日志因 PG 仍绑 L1 被拒 → 副作用与持久状态分裂

## 修复

_bind_lease_generation 改 CAS 谓词:同 worker(worker_id CAS)一律接受
incoming lease_id (caller 已通过 lease_validator 保证 incoming 是 current)。
跨 worker 仍拒(worker_id CAS 天然不匹配)。

本测试锁死:
1. L1 → L2 同 worker 换代 update 成功
2. 跨 worker (worker_id 不匹配) update 拒
3. 未绑 worker 场景走原 CAS 分支
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_core.application.services.task_run_service import TaskRunService


@pytest.mark.asyncio
async def test_bind_allows_l1_to_l2_same_worker():
    """P1-GW-03: 同 worker L1 → L2 换代 (Direct takeover 场景)。"""
    service = TaskRunService(lease_validator=AsyncMock(return_value=True))
    execution = SimpleNamespace(run_id="r-1", worker_id=42, lease_id="L1")

    with patch("antcode_core.application.services.task_run_service.TaskRun") as MockTR:
        # filter().update() 返回 1 表示同 worker 换代成功
        filter_mock = MagicMock()
        filter_mock.update = AsyncMock(return_value=1)
        MockTR.filter = MagicMock(return_value=filter_mock)

        ok = await service._bind_lease_generation(execution, {"lease_id": "L2"})

        assert ok is True
        # 关键:filter 用 (run_id, worker_id) 限定同 worker
        MockTR.filter.assert_called_once_with(run_id="r-1", worker_id=42)
        # update 直接改 lease_id 到 incoming (不再需 lease_id CAS 谓词)
        filter_mock.update.assert_awaited_once_with(lease_id="L2")


@pytest.mark.asyncio
async def test_bind_rejects_cross_worker():
    """P1-GW-03: 跨 worker (execution.worker_id != current worker) 由 CAS 天然拒。"""
    service = TaskRunService(lease_validator=AsyncMock(return_value=True))
    # execution 绑定 worker=42
    execution = SimpleNamespace(run_id="r-1", worker_id=42, lease_id="L1")

    with patch("antcode_core.application.services.task_run_service.TaskRun") as MockTR:
        filter_mock = MagicMock()
        # 跨 worker filter 匹配 0 行, update 返回 0
        filter_mock.update = AsyncMock(return_value=0)
        MockTR.filter = MagicMock(return_value=filter_mock)

        ok = await service._bind_lease_generation(execution, {"lease_id": "L2"})

        assert ok is False


@pytest.mark.asyncio
async def test_bind_unbound_worker_falls_back_to_lease_cas():
    """P1-GW-03: execution.worker_id 缺失(测试 fixture)时走原 CAS 分支。"""
    service = TaskRunService(lease_validator=AsyncMock(return_value=True))
    execution = SimpleNamespace(run_id="r-1", lease_id=None)  # 无 worker_id

    with patch("antcode_core.application.services.task_run_service.TaskRun") as MockTR:
        filter_mock = MagicMock()
        filter_mock.filter = MagicMock(return_value=filter_mock)
        filter_mock.update = AsyncMock(return_value=1)
        MockTR.filter = MagicMock(return_value=filter_mock)

        ok = await service._bind_lease_generation(execution, {"lease_id": "L1"})

        assert ok is True
        # 走原 CAS 分支(filter(run_id).filter(Q lease_id_isnull | eq))
        MockTR.filter.assert_called_once_with(run_id="r-1")


@pytest.mark.asyncio
async def test_bind_rejects_empty_or_too_long_lease():
    """P1-GW-03 反面:incoming lease_id 空或超 64 字符 → 拒(不 CAS)。"""
    service = TaskRunService(lease_validator=AsyncMock(return_value=True))
    execution = SimpleNamespace(run_id="r-1", worker_id=42, lease_id="L1")

    # 空 lease_id
    ok = await service._bind_lease_generation(execution, {"lease_id": ""})
    assert ok is False

    # 超长 lease_id
    ok = await service._bind_lease_generation(execution, {"lease_id": "x" * 65})
    assert ok is False
