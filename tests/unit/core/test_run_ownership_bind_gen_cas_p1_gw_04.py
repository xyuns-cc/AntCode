"""P1-GW-04 回归:bind_worker_run_lease_generation lease_gen 单调 CAS。

审查文档 docs/code-review-2026-07-22-round3-review.md 的 P1-GW-04:
Redis fence 后再写 PG 形成反向竞态。原 bind 只按 (run_id, worker_id)
更新 lease_id,若 L1 fence 返回 ACQUIRED 后异常暂停(超 Lease TTL),
L2 完成 fence+bind 后 L1 迟到 bind 会把 PG 从 L2 覆盖回 L1。

修复:新增 TaskRun.lease_gen(BIGINT NULL)列 + bind 支持 lease_gen 参数,
CAS 谓词 `lease_gen IS NULL OR lease_gen <= NEW.lease_gen` 拒绝旧代际
覆盖新代际。

本测试锁死:
1. lease_gen=None 时保持原兼容行为
2. lease_gen 递增时 bind 成功,写入新 lease_id + lease_gen
3. lease_gen 小于当前 gen 时 CAS 拒绝(抛 PermissionError)
4. lease_gen<0 抛 ValueError
5. run 不存在与 CAS 失败区分不同错误消息
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_bind_lease_gen_none_preserves_original_compat_path():
    """P1-GW-04:lease_gen=None 走兼容路径,不做 gen CAS。"""
    from antcode_core.application.services.workers import run_ownership_service as svc

    resolved_worker = MagicMock(id=42)
    with patch.object(svc, "_resolve_worker", AsyncMock(return_value=resolved_worker)):
        # 用真实 filter 会走 tortoise ORM,单测里 mock 掉
        filter_mock = MagicMock()
        filter_mock.update = AsyncMock(return_value=1)
        with patch.object(svc.TaskRun, "filter", MagicMock(return_value=filter_mock)) as pf:
            await svc.bind_worker_run_lease_generation("worker-1", "run-1", lease_id="lease-1", lease_gen=None)

            # 兼容路径:只传 (run_id, worker_id), update 只带 lease_id
            pf.assert_called_once_with(run_id="run-1", worker_id=42)
            filter_mock.update.assert_awaited_once_with(lease_id="lease-1")


@pytest.mark.asyncio
async def test_bind_lease_gen_positive_writes_gen_with_cas():
    """P1-GW-04:传入正 lease_gen 时走 CAS 路径。"""
    from antcode_core.application.services.workers import run_ownership_service as svc
    from tortoise.expressions import Q

    resolved_worker = MagicMock(id=42)
    with patch.object(svc, "_resolve_worker", AsyncMock(return_value=resolved_worker)):
        filter_mock = MagicMock()
        filter_mock.update = AsyncMock(return_value=1)
        with patch.object(svc.TaskRun, "filter", MagicMock(return_value=filter_mock)) as pf:
            await svc.bind_worker_run_lease_generation(
                "worker-1", "run-1", lease_id="lease-2", lease_gen=1_700_000_000_000
            )

            # CAS 路径:filter 第一个参数是 Q 表达式, kwargs 包含 run_id/worker_id
            call = pf.call_args
            assert len(call.args) == 1
            q = call.args[0]
            assert isinstance(q, Q)
            assert call.kwargs == {"run_id": "run-1", "worker_id": 42}
            filter_mock.update.assert_awaited_once_with(lease_id="lease-2", lease_gen=1_700_000_000_000)


@pytest.mark.asyncio
async def test_bind_lease_gen_cas_conflict_raises_permission_error():
    """P1-GW-04 关键:CAS 拒绝(update=0)且 run 存在 → PermissionError 说明 gen 冲突。"""
    from antcode_core.application.services.workers import run_ownership_service as svc

    resolved_worker = MagicMock(id=42)

    filter_calls: list = []

    def mock_filter(*args, **kwargs):
        filter_calls.append((args, kwargs))
        m = MagicMock()
        if len(args) > 0:  # 第一次: Q + kwargs, update 返回 0
            m.update = AsyncMock(return_value=0)
        else:  # 第二次(区分是否存在): 只按 kwargs, exists 返回 True
            m.exists = AsyncMock(return_value=True)
        return m

    with patch.object(svc, "_resolve_worker", AsyncMock(return_value=resolved_worker)):
        with patch.object(svc.TaskRun, "filter", side_effect=mock_filter):
            with pytest.raises(PermissionError, match="lease_gen 单调 CAS 失败"):
                await svc.bind_worker_run_lease_generation("worker-1", "run-1", lease_id="lease-old", lease_gen=100)

    assert len(filter_calls) == 2


@pytest.mark.asyncio
async def test_bind_lease_gen_missing_run_raises_specific_permission_error():
    """P1-GW-04:update=0 且 run 不存在 → 明确的"不存在或不属于当前 Worker"。"""
    from antcode_core.application.services.workers import run_ownership_service as svc

    resolved_worker = MagicMock(id=42)

    def mock_filter(*args, **kwargs):
        m = MagicMock()
        if len(args) > 0:
            m.update = AsyncMock(return_value=0)
        else:
            m.exists = AsyncMock(return_value=False)
        return m

    with patch.object(svc, "_resolve_worker", AsyncMock(return_value=resolved_worker)):
        with patch.object(svc.TaskRun, "filter", side_effect=mock_filter):
            with pytest.raises(PermissionError, match="不存在或不属于当前 Worker"):
                await svc.bind_worker_run_lease_generation("worker-1", "run-1", lease_id="lease-1", lease_gen=100)


@pytest.mark.asyncio
async def test_bind_negative_lease_gen_raises_value_error():
    """P1-GW-04:lease_gen<0 参数校验失败。"""
    from antcode_core.application.services.workers import run_ownership_service as svc

    with pytest.raises(ValueError, match="lease_gen 必须非负"):
        await svc.bind_worker_run_lease_generation("worker-1", "run-1", lease_id="lease-1", lease_gen=-1)


def test_task_run_model_has_lease_gen_field():
    """P1-GW-04:TaskRun ORM 模型必须暴露 lease_gen 字段。"""
    from antcode_core.domain.models.task_run import TaskRun

    fields = TaskRun._meta.fields_map
    assert "lease_gen" in fields
    # 应可为 null
    assert fields["lease_gen"].null is True
