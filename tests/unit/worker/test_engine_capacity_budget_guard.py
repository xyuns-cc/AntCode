"""运行时改限额同样不许超卖容器内存预算。

启动期校验只管得住启动那一刻的配置；控制台/API 事后把 ``task_memory_limit_mb``
调大同样能把 并发 × 限额 顶过容器额度，而且 payload 里根本没有并发字段——
生效并发是引擎当前的 ``_max_concurrent``。

证伪项：拿掉 ``resolve_engine_config_update`` 里的 ``validate_capacity_fits_budget``
调用，第一条用例变红；第二条是对照组，防止"全拒"被当成"全过"。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker import adaptive_limits as adaptive_mod
from antcode_worker.engine.engine import Engine
from antcode_worker.resource_budget import BudgetSource, MemoryBudget, ResourceBudgetError

_BYTES_PER_MIB = 1024 * 1024
# 预算钉死，让用例只检验配置逻辑而不检验跑测机器规格：任务池 = 65536 × 0.7 = 45875MB
_PINNED_BUDGET_BYTES = 64 * 1024 * _BYTES_PER_MIB

_OVERSELL_CONCURRENCY = 20
_OVERSELL_MEMORY_LIMIT_MB = 8192
_FITTING_CONCURRENCY = 2
_FITTING_MEMORY_LIMIT_MB = 1024


@pytest.fixture(autouse=True)
def _pinned_memory_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        adaptive_mod,
        "current_memory_budget",
        lambda: MemoryBudget(
            total_bytes=_PINNED_BUDGET_BYTES,
            source=BudgetSource.CGROUP_V2,
            origin="test",
        ),
    )


def _engine(max_concurrent: int) -> Engine:
    transport = MagicMock(is_connected=True)
    transport._worker_id = "worker-test"
    transport._lease_id = "lease-test"
    transport.ack_control = AsyncMock(return_value=True)
    executor = MagicMock()
    executor.has_task.return_value = False
    executor.cancel = AsyncMock(return_value=False)
    executor.resize_concurrency = AsyncMock()
    return Engine(transport=transport, executor=executor, max_concurrent=max_concurrent)


@pytest.mark.asyncio
async def test_runtime_update_rejects_capacity_that_oversells_the_budget() -> None:
    """20 路 × 8192MB = 163840MB 放不进 45875MB 任务池，必须拒绝且不半生效。"""
    engine = _engine(_OVERSELL_CONCURRENCY)

    with pytest.raises(ResourceBudgetError, match="超卖"):
        await engine.apply_config_update({"task_memory_limit_mb": _OVERSELL_MEMORY_LIMIT_MB})

    assert engine._policies.resource.memory_limit_mb != _OVERSELL_MEMORY_LIMIT_MB, "被拒的更新不得半生效"


@pytest.mark.asyncio
async def test_runtime_update_accepts_capacity_that_fits() -> None:
    """对照组：同一条路径上放得进预算的更新必须生效。"""
    engine = _engine(_FITTING_CONCURRENCY)

    await engine.apply_config_update({"task_memory_limit_mb": _FITTING_MEMORY_LIMIT_MB})

    assert engine._policies.resource.memory_limit_mb == _FITTING_MEMORY_LIMIT_MB
