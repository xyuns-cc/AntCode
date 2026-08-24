"""打分链路的两端：调度真的按它排序，接口报的也真的是它。

上一份用例只证到纯函数。这份把它接到两条实际链路上：

- ``select_best_worker`` —— 派发路径。压力小的那台必须被选中，压力大的那台必须被避开；
- ``/workers/load/best`` —— 只读接口。它曾经写 ``calculate_load_score(best_worker)``，
  把 Worker 对象喂给了 ``metrics`` 形参，于是恒走"无指标"分支，报出来的 ``load_score``
  永远是满分 100，与选中的是哪台、多闲，全无关系。

**证伪方式**：分别退掉 ``workers_query.get_best_worker`` 的 ``score_worker`` 调用、
以及 ``get_workers_ranking`` 的 ``heartbeat_age_ms`` 字段，对应用例变红。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers.worker_dispatcher import WorkerLoadBalancer
from antcode_core.application.services.workers.worker_liveness import HEARTBEAT_AGE_UNKNOWN_MS
from antcode_core.application.services.workers.worker_load_score import PERCENT_FULL
from antcode_core.domain.models.enums import WorkerStatus

_READY_FILTER = "antcode_core.application.services.workers.worker_dispatcher.filter_registration_ready_workers"

_MAX_TASKS = 10
# 三台都在正常干活的量级：各自吃掉自己配额的两成到七成，全都远低于 90 的硬门禁。
_IDLE = {"cpu": 15.0, "memory": 20.0}
_BUSY = {"cpu": 70.0, "memory": 65.0}
_MIDDLING = {"cpu": 40.0, "memory": 45.0}

_HEARTBEAT_AGE_SEC = 12
_MS_PER_SEC = 1000
_AGE_TOLERANCE_MS = 5000


def _worker(worker_id: int, name: str, *, heartbeat: datetime | None = None):
    return SimpleNamespace(
        id=worker_id,
        public_id=f"worker-{worker_id}",
        name=name,
        host="10.0.0.1",
        port=8000,
        region=None,
        status=WorkerStatus.ONLINE,
        capabilities={},
        metrics={},
        last_heartbeat=heartbeat,
    )


def _metrics(profile: dict[str, float], *, running: int = 0):
    return {
        "cpu": profile["cpu"],
        "memory": profile["memory"],
        "runningTasks": running,
        "queuedTasks": 0,
        "maxConcurrentTasks": _MAX_TASKS,
    }


def _balancer_over(monkeypatch, by_worker_id: dict[int, dict]):
    balancer = WorkerLoadBalancer()

    async def refresh(worker):
        return by_worker_id[worker.id]

    monkeypatch.setattr(balancer, "_refresh_resources", refresh)
    return balancer


@pytest.mark.asyncio
async def test_dispatch_picks_the_least_loaded_and_avoids_the_most_loaded(monkeypatch) -> None:
    """控制组：一台必须被选中（idle），一台必须被避开（busy），中间那台两头都不是。"""
    idle, middling, busy = _worker(1, "idle"), _worker(2, "middling"), _worker(3, "busy")
    workers = [busy, middling, idle]  # 故意把最忙的排在最前，排序不能只是"取第一个"
    balancer = _balancer_over(
        monkeypatch,
        {idle.id: _metrics(_IDLE), middling.id: _metrics(_MIDDLING), busy.id: _metrics(_BUSY)},
    )
    monkeypatch.setattr(_READY_FILTER, AsyncMock(return_value=workers))

    selected = await balancer.select_best_worker(workers=workers)

    assert selected is idle
    assert selected is not busy


@pytest.mark.asyncio
async def test_normal_load_does_not_evict_anyone(monkeypatch) -> None:
    """多台都在正常干活时，不该有 Worker 被判不可用——换权重不得误伤。

    权重只影响排序、不进 ``is_worker_available``，这条就是把那句话钉住。
    """
    idle, middling, busy = _worker(1, "idle"), _worker(2, "middling"), _worker(3, "busy")
    balancer = _balancer_over(
        monkeypatch,
        {idle.id: _metrics(_IDLE), middling.id: _metrics(_MIDDLING), busy.id: _metrics(_BUSY)},
    )

    for worker, profile in ((idle, _IDLE), (middling, _MIDDLING), (busy, _BUSY)):
        assert balancer.is_worker_available(worker, _metrics(profile)) is True, f"{worker.name} 被误伤"


@pytest.mark.asyncio
async def test_best_worker_api_reports_the_score_it_ranked_on(monkeypatch) -> None:
    """接口报的分必须是刚才排序用的那个，不是恒定的满分 100。"""
    from antcode_web_api.routes.v1 import workers_query

    idle, busy = _worker(1, "idle"), _worker(3, "busy")
    balancer = _balancer_over(monkeypatch, {idle.id: _metrics(_IDLE), busy.id: _metrics(_BUSY)})
    monkeypatch.setattr(balancer, "select_best_worker", AsyncMock(return_value=idle))
    monkeypatch.setattr(
        "antcode_core.application.services.workers.worker_load_balancer",
        balancer,
        raising=False,
    )

    response = await workers_query.get_best_worker(
        region=None,
        tags=None,
        require_render=False,
        worker_to_response=lambda worker: SimpleNamespace(model_dump=lambda: {"name": worker.name}),
    )

    score = response.data["load_score"]
    assert score != PERCENT_FULL, "恒报满分说明 metrics 又没被传进去"
    assert score == pytest.approx(sum(_IDLE.values()) / 3, abs=0.01)


@pytest.mark.asyncio
async def test_ranking_reports_heartbeat_age_not_a_latency_that_was_never_measured(monkeypatch) -> None:
    """排名里那个数是心跳年龄。从没有心跳的节点必须报"没有"，不是 0。"""
    beating = _worker(1, "beating", heartbeat=datetime.now() - timedelta(seconds=_HEARTBEAT_AGE_SEC))
    silent = _worker(2, "silent", heartbeat=None)
    balancer = _balancer_over(monkeypatch, {beating.id: _metrics(_IDLE), silent.id: _metrics(_IDLE)})
    monkeypatch.setattr(
        "antcode_core.domain.models.Worker.filter",
        lambda **_kwargs: SimpleNamespace(all=AsyncMock(return_value=[beating, silent])),
    )

    rankings = {row["name"]: row for row in await balancer.get_workers_ranking()}

    assert rankings["beating"]["heartbeat_age_ms"] == pytest.approx(
        _HEARTBEAT_AGE_SEC * _MS_PER_SEC, abs=_AGE_TOLERANCE_MS
    )
    assert rankings["silent"]["heartbeat_age_ms"] == HEARTBEAT_AGE_UNKNOWN_MS
