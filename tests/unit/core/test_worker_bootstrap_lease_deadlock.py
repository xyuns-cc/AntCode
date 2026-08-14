"""新注册 Worker 的首租约引导窗口回归测试。

背景（真实环境复现的 P0）：Lease 资格白名单是 ``{connecting, online}``
（``worker_lease_authority.LEASE_ELIGIBLE_STATUSES``），而 Worker 必须先拿到
Lease 才会开始上报心跳。心跳监控原先把"从未上报过心跳"一律判为离线，于是新
注册的 Worker 在注册后约 1 秒就被从引导态 ``connecting`` 打成 ``offline``，
此后 ``POST /workers/{id}/direct-control/lease`` 恒返回 409 Conflict——
拿不到 Lease → 不会有心跳 → 永远不合格，闭合死锁，任何 Worker 都无法启动。

这些用例锁住修复后的两条边界：引导窗口内不降级、窗口耗尽后照常降级。
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from antcode_core.application.services.workers.worker_heartbeat_service import (
    WorkerHeartbeatService,
)
from antcode_core.domain.models import Worker, WorkerStatus
from tortoise import Tortoise


@pytest_asyncio.fixture(autouse=True)
async def database(tmp_path):
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'worker-bootstrap.sqlite3'}",
        modules={
            "models": [
                "antcode_core.domain.models.worker",
                "antcode_core.domain.models.worker_install_key",
            ]
        },
        use_tz=True,
        timezone="UTC",
    )
    await Tortoise.generate_schemas()
    yield
    await Tortoise.close_connections()
    await Tortoise._reset_apps()


async def _create_connecting_worker(name: str) -> Worker:
    return await Worker.create(
        name=name,
        host="127.0.0.1",
        port=8001,
        status=WorkerStatus.CONNECTING.value,
        transport_mode="direct",
    )


def _service() -> WorkerHeartbeatService:
    return WorkerHeartbeatService()


@pytest.mark.asyncio
async def test_fresh_worker_keeps_connecting_so_it_can_obtain_first_lease():
    """刚注册、还没有心跳的 Worker 必须留在 connecting，否则永远拿不到首个 Lease。"""
    worker = await _create_connecting_worker("bootstrap-fresh")
    service = _service()
    state = {"failures": 0, "next_check": datetime.now(), "suspended": False}

    await service._handle_worker_offline(worker, state, WorkerStatus.CONNECTING.value)

    refreshed = await Worker.get(id=worker.id)
    assert refreshed.status == WorkerStatus.CONNECTING.value


@pytest.mark.asyncio
async def test_bootstrap_window_is_bounded_and_still_marks_dead_worker_offline():
    """引导窗口是有界的：注册后就挂掉的 Worker 最终仍要被标记离线。"""
    worker = await _create_connecting_worker("bootstrap-expired")
    service = _service()
    stale = datetime.now(UTC) - timedelta(seconds=service.HEARTBEAT_TIMEOUT + 60)
    await Worker.filter(id=worker.id).update(updated_at=stale, created_at=stale)
    worker = await Worker.get(id=worker.id)
    state = {"failures": 0, "next_check": datetime.now(), "suspended": False}

    await service._handle_worker_offline(worker, state, WorkerStatus.CONNECTING.value)

    refreshed = await Worker.get(id=worker.id)
    assert refreshed.status == WorkerStatus.OFFLINE.value


@pytest.mark.asyncio
async def test_worker_with_prior_heartbeat_is_not_protected_by_bootstrap_window():
    """引导窗口只保护"从未上报过心跳"的 Worker，掉线的老 Worker 照常降级。"""
    worker = await _create_connecting_worker("bootstrap-had-heartbeat")
    worker.last_heartbeat = datetime.now(UTC) - timedelta(hours=1)
    await worker.save()
    state = {"failures": 0, "next_check": datetime.now(), "suspended": False}

    await _service()._handle_worker_offline(worker, state, WorkerStatus.CONNECTING.value)

    refreshed = await Worker.get(id=worker.id)
    assert refreshed.status == WorkerStatus.OFFLINE.value


@pytest.mark.asyncio
async def test_restarted_offline_worker_can_still_obtain_a_lease():
    """重启后被标记 offline 的 Worker 必须仍可申请 Lease，否则永远回不来。

    ``offline`` 是心跳监控的观测结果而非运维意图；管理员停用由 Redis lifecycle
    围栏（LeaseStore.disable_worker）独立保证，不依赖这里的状态白名单。
    """
    from antcode_core.application.services.workers.worker_lease_authority import (
        LEASE_ELIGIBLE_STATUSES,
    )

    assert WorkerStatus.OFFLINE.value in LEASE_ELIGIBLE_STATUSES
    assert WorkerStatus.MAINTENANCE.value not in LEASE_ELIGIBLE_STATUSES
