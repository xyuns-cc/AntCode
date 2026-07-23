"""P1-GW-02 (round6) 回归:ownership bind 前查 TaskRun 终态,已终态一律拒绝。

审查文档 round6 P1-GW-02:
`ownership claim 不检查 TaskRun 终态; Worker 先报终态再 ACK; 终态持久化
后 ACK 丢失, L2 仍可 reclaim + claim 并启动进程 → 已完成 run 重复执行`。

修复:bind_worker_run_lease_generation 在真正 update 前 filter().only(
"id","status","worker_id").first() 一次, status ∈ terminal 集合就 raise
PermissionError, 阻止 L2 重跑已终态 run。

本测试锁死:
1. TaskRun status ∈ {success/failed/cancelled/timeout/skipped/rejected} 都拒
2. 非终态状态(pending/queued/dispatching/running)正常放行
3. TaskRun 不存在时不因终态判死走错分支
4. lease_gen=None 兼容路径也走终态检查(而不是绕过)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_filter_for(status: str | None, update_return: int = 1):
    """构造既支持终态探针又支持 update 的 filter mock。

    Args:
        status: 终态探针返回的 TaskRun.status; None 表示 run 不存在
        update_return: filter(...).update(...) 返回值
    """
    if status is None:
        existing = None
    else:
        existing = MagicMock(id=1, worker_id=42, status=status)
    only_chain = MagicMock()
    only_chain.first = AsyncMock(return_value=existing)

    def _filter(*args, **kwargs):
        m = MagicMock()
        m.only = MagicMock(return_value=only_chain)
        m.update = AsyncMock(return_value=update_return)
        return m

    return _filter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_status",
    ["success", "failed", "cancelled", "timeout", "skipped", "rejected"],
)
async def test_bind_rejects_all_terminal_statuses(terminal_status):
    """P1-GW-02:所有 6 个终态都必须拒 bind,防重复执行。"""
    from antcode_core.application.services.workers import run_ownership_service as svc

    resolved_worker = MagicMock(id=42)
    with patch.object(svc, "_resolve_worker", AsyncMock(return_value=resolved_worker)):
        with patch.object(svc.TaskRun, "filter", side_effect=_mock_filter_for(terminal_status)):
            with pytest.raises(PermissionError, match="已在终态"):
                await svc.bind_worker_run_lease_generation(
                    "worker-1", "run-1", lease_id="lease-x", lease_gen=100
                )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "non_terminal_status",
    ["pending", "queued", "dispatching", "running"],
)
async def test_bind_passes_non_terminal_statuses(non_terminal_status):
    """P1-GW-02 反面:非终态状态放行(否则 L1 正常 claim 也被拒)。"""
    from antcode_core.application.services.workers import run_ownership_service as svc

    resolved_worker = MagicMock(id=42)
    with patch.object(svc, "_resolve_worker", AsyncMock(return_value=resolved_worker)):
        with patch.object(svc.TaskRun, "filter", side_effect=_mock_filter_for(non_terminal_status)):
            # 不 raise,正常 update
            await svc.bind_worker_run_lease_generation(
                "worker-1", "run-1", lease_id="lease-x", lease_gen=100
            )


@pytest.mark.asyncio
async def test_bind_when_taskrun_absent_falls_through_to_update():
    """P1-GW-02:run 不存在时不走终态分支,继续走 update(update=0 会触发另一路 PermissionError)。"""
    from antcode_core.application.services.workers import run_ownership_service as svc

    resolved_worker = MagicMock(id=42)

    def _filter(*args, **kwargs):
        m = MagicMock()
        # 探针 first() 返回 None (run 不存在)
        only_chain = MagicMock()
        only_chain.first = AsyncMock(return_value=None)
        m.only = MagicMock(return_value=only_chain)
        # update 返回 0
        m.update = AsyncMock(return_value=0)
        # exists 也返回 False
        m.exists = AsyncMock(return_value=False)
        return m

    with patch.object(svc, "_resolve_worker", AsyncMock(return_value=resolved_worker)):
        with patch.object(svc.TaskRun, "filter", side_effect=_filter):
            with pytest.raises(PermissionError, match="不存在或不属于当前 Worker"):
                await svc.bind_worker_run_lease_generation(
                    "worker-1", "run-1", lease_id="lease-x", lease_gen=100
                )


@pytest.mark.asyncio
async def test_bind_compat_path_also_checks_terminal(monkeypatch):
    """P1-GW-02:lease_gen=None 兼容路径也走终态检查,不能被绕过。"""
    from antcode_core.application.services.workers import run_ownership_service as svc

    resolved_worker = MagicMock(id=42)
    with patch.object(svc, "_resolve_worker", AsyncMock(return_value=resolved_worker)):
        with patch.object(svc.TaskRun, "filter", side_effect=_mock_filter_for("success")):
            with pytest.raises(PermissionError, match="已在终态"):
                await svc.bind_worker_run_lease_generation(
                    "worker-1", "run-1", lease_id="lease-x", lease_gen=None
                )
