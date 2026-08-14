"""B12: 4 个派发入口必须在权威 Master 代际下派发，读不到权威即显式失败。

``publish_ready_batch`` 已经把 Master 代际改成必传。本文件验证剩下的接线：
- 非 Master 身份的入口从 ``scheduler_authority`` 回读权威代际；
- 表里没有活跃权威时，每个入口都显式失败而不是无栅栏放行；
- Master 自己的补派 loop 用本进程的 Leader 代际，不多打一次 DB。

core 的 ``SchedulerService`` 已不再持有派发路径（执行权威唯一归 Master 的
``control.scheduler_loop``），因此这里不再有 core 内嵌调度器的入口。

环境搭建见 ``dispatch_epoch_support``；被测的是真实入口函数与真实的
``require_active_scheduler_epoch``，两者都没有被 mock 掉。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from antcode_core.application.services.scheduler_authority_epoch import (
    SchedulerAuthorityUnavailableError,
    require_active_scheduler_epoch,
)
from antcode_core.application.services.workers.worker_dispatcher import (
    BatchDispatchResult,
    DispatchResult,
)
from antcode_master.control.redispatch_loop import RedispatchLoop
from antcode_web_api.routes.v1 import workers
from fastapi import HTTPException, status

from tests.unit.core.dispatch_epoch_support import (
    LEADER_EPOCH,
    OWNER_ID,
    EpochRecorder,
    batch_request,
    patch_batch_dispatch_deps,
    patch_crawl_batch,
    patch_dispatch_task,
    patch_leader_epoch,
    patch_single_dispatch_deps,
    redispatch_payload,
    single_request,
)

DISPATCH_OK = SimpleNamespace(success=True)


# ---------------------------------------------------------------------------
# 读取接口本身
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_active_scheduler_epoch_returns_the_persisted_token(active_scheduler_authority) -> None:
    assert await require_active_scheduler_epoch() == active_scheduler_authority


@pytest.mark.asyncio
async def test_require_active_scheduler_epoch_fails_closed_without_authority(empty_scheduler_authority) -> None:
    with pytest.raises(SchedulerAuthorityUnavailableError, match="scheduler_authority"):
        await require_active_scheduler_epoch()


# ---------------------------------------------------------------------------
# 入口 1/2：Web API 手动单任务 / 批量派发
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_api_single_dispatch_uses_the_authoritative_epoch(monkeypatch, active_scheduler_authority) -> None:
    patch_single_dispatch_deps(monkeypatch)
    recorder = patch_dispatch_task(monkeypatch, DispatchResult(success=True))

    await workers.dispatch_task_to_worker(single_request(), SimpleNamespace(user_id=OWNER_ID))

    assert recorder.epochs == [active_scheduler_authority]


@pytest.mark.asyncio
async def test_web_api_single_dispatch_is_rejected_without_authority(monkeypatch, empty_scheduler_authority) -> None:
    patch_single_dispatch_deps(monkeypatch)
    recorder = patch_dispatch_task(monkeypatch, DispatchResult(success=True))

    with pytest.raises(HTTPException) as raised:
        await workers.dispatch_task_to_worker(single_request(), SimpleNamespace(user_id=OWNER_ID))

    assert raised.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "scheduler_authority" in str(raised.value.detail)
    assert recorder.epochs == []


@pytest.mark.asyncio
async def test_web_api_batch_dispatch_uses_the_authoritative_epoch(monkeypatch, active_scheduler_authority) -> None:
    recorder = EpochRecorder(BatchDispatchResult(success=True))
    patch_batch_dispatch_deps(monkeypatch, recorder)

    await workers.dispatch_batch_to_worker(batch_request(), SimpleNamespace(user_id=OWNER_ID))

    assert recorder.epochs == [active_scheduler_authority]


@pytest.mark.asyncio
async def test_web_api_batch_dispatch_is_rejected_without_authority(monkeypatch, empty_scheduler_authority) -> None:
    recorder = EpochRecorder(BatchDispatchResult(success=True))
    patch_batch_dispatch_deps(monkeypatch, recorder)

    with pytest.raises(HTTPException) as raised:
        await workers.dispatch_batch_to_worker(batch_request(), SimpleNamespace(user_id=OWNER_ID))

    assert raised.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert recorder.epochs == []


# ---------------------------------------------------------------------------
# 入口 3：爬虫批次派发
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crawl_batch_dispatch_uses_the_authoritative_epoch(monkeypatch, active_scheduler_authority) -> None:
    service = patch_crawl_batch(monkeypatch)
    recorder = patch_dispatch_task(monkeypatch, DISPATCH_OK)

    await service._on_batch_started("batch-1")

    assert recorder.epochs == [active_scheduler_authority]


@pytest.mark.asyncio
async def test_crawl_batch_dispatch_fails_closed_without_authority(monkeypatch, empty_scheduler_authority) -> None:
    service = patch_crawl_batch(monkeypatch)
    recorder = patch_dispatch_task(monkeypatch, DISPATCH_OK)

    with pytest.raises(SchedulerAuthorityUnavailableError):
        await service._on_batch_started("batch-1")

    assert recorder.epochs == []


# ---------------------------------------------------------------------------
# 入口 4：Master 补派 loop（用自己的 Leader 代际）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_master_redispatch_uses_its_own_leader_epoch_not_the_table(
    monkeypatch,
    active_scheduler_authority,
) -> None:
    """权威表里是另一个代际；补派记录到 Leader 代际即证明它没有回读 DB。"""
    patch_leader_epoch(monkeypatch, token=LEADER_EPOCH)
    recorder = patch_dispatch_task(monkeypatch, DISPATCH_OK)

    await RedispatchLoop()._redispatch(redispatch_payload(), project_id="project-1", run_id="run-1")

    assert LEADER_EPOCH != active_scheduler_authority
    assert recorder.epochs == [LEADER_EPOCH]


@pytest.mark.asyncio
async def test_master_redispatch_fails_closed_when_leadership_is_lost(monkeypatch) -> None:
    patch_leader_epoch(monkeypatch, error=RuntimeError("当前实例不是 Redis 权威 Leader"))
    recorder = patch_dispatch_task(monkeypatch, DISPATCH_OK)

    with pytest.raises(RuntimeError, match="Leader"):
        await RedispatchLoop()._redispatch(redispatch_payload(), project_id="project-1", run_id="run-1")

    assert recorder.epochs == []
