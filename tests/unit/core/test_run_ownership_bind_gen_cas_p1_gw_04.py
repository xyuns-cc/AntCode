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


_EXPECTED_FILTER_CALLS = 2  # P1-GW-02: 1 次终态探针 + 1 次 update


def _make_bind_filter_mock(status: str = "running", update_return: int = 1):
    """P1-GW-02 后 bind 需先 filter().only().first() 判终态,再走 update。

    返回一个 MagicMock, 支持:
    - filter(...).only(...).first() → MagicMock(status=status)
    - filter(...).update(...) → update_return
    """
    existing = MagicMock(id=1, worker_id=42, status=status)
    only_chain = MagicMock()
    only_chain.first = AsyncMock(return_value=existing)
    filter_mock = MagicMock()
    filter_mock.only = MagicMock(return_value=only_chain)
    filter_mock.update = AsyncMock(return_value=update_return)
    return filter_mock


@pytest.mark.asyncio
async def test_bind_lease_gen_none_preserves_original_compat_path():
    """P1-GW-04:lease_gen=None 走兼容路径,不做 gen CAS。"""
    from antcode_core.application.services.workers import run_ownership_service as svc

    resolved_worker = MagicMock(id=42)
    with patch.object(svc, "_resolve_worker", AsyncMock(return_value=resolved_worker)):
        filter_mock = _make_bind_filter_mock()
        with patch.object(svc.TaskRun, "filter", MagicMock(return_value=filter_mock)) as pf:
            await svc.bind_worker_run_lease_generation("worker-1", "run-1", lease_id="lease-1", lease_gen=None)

            # 兼容路径:调 filter 2 次(第一次判终态,第二次 update)
            # 第 2 次调用: filter(run_id="run-1", worker_id=42).update(lease_id="lease-1")
            assert pf.call_count == _EXPECTED_FILTER_CALLS
            assert pf.call_args_list[-1].kwargs == {"run_id": "run-1", "worker_id": 42}
            filter_mock.update.assert_awaited_once_with(lease_id="lease-1")


@pytest.mark.asyncio
async def test_bind_lease_gen_positive_writes_gen_with_cas():
    """P1-GW-04:传入正 lease_gen 时走 CAS 路径。"""
    from antcode_core.application.services.workers import run_ownership_service as svc
    from tortoise.expressions import Q

    resolved_worker = MagicMock(id=42)
    with patch.object(svc, "_resolve_worker", AsyncMock(return_value=resolved_worker)):
        filter_mock = _make_bind_filter_mock()
        with patch.object(svc.TaskRun, "filter", MagicMock(return_value=filter_mock)) as pf:
            await svc.bind_worker_run_lease_generation(
                "worker-1", "run-1", lease_id="lease-2", lease_gen=1_700_000_000_000
            )

            # CAS 路径:第 1 次 filter(run_id=...) 判终态,第 2 次 filter(Q, ...) CAS
            assert pf.call_count == _EXPECTED_FILTER_CALLS
            cas_call = pf.call_args_list[-1]
            assert len(cas_call.args) == 1
            q = cas_call.args[0]
            assert isinstance(q, Q)
            assert cas_call.kwargs == {"run_id": "run-1", "worker_id": 42}
            filter_mock.update.assert_awaited_once_with(lease_id="lease-2", lease_gen=1_700_000_000_000)


@pytest.mark.asyncio
async def test_bind_rejects_terminal_taskrun():
    """P1-GW-02 (round6): TaskRun 已终态时 bind 拒绝,防重复 claim 执行。"""
    from antcode_core.application.services.workers import run_ownership_service as svc

    resolved_worker = MagicMock(id=42)
    filter_mock = _make_bind_filter_mock(status="success")
    with patch.object(svc, "_resolve_worker", AsyncMock(return_value=resolved_worker)):
        with patch.object(svc.TaskRun, "filter", MagicMock(return_value=filter_mock)):
            with pytest.raises(PermissionError, match="已在终态"):
                await svc.bind_worker_run_lease_generation(
                    "worker-1", "run-1", lease_id="lease-x", lease_gen=100
                )
    # update 不应被调用
    filter_mock.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_bind_lease_gen_cas_conflict_raises_permission_error():
    """P1-GW-04 关键:CAS 拒绝(update=0)且 run 存在 → PermissionError 说明 gen 冲突。"""
    from antcode_core.application.services.workers import run_ownership_service as svc

    resolved_worker = MagicMock(id=42)

    filter_calls: list = []

    def mock_filter(*args, **kwargs):
        filter_calls.append((args, kwargs))
        m = MagicMock()
        if len(args) > 0:  # CAS 分支: Q + kwargs, update 返回 0
            m.update = AsyncMock(return_value=0)
        elif "run_id" in kwargs and "worker_id" not in kwargs:
            # P1-GW-02 终态探针: filter(run_id=...).only(...).first() → 非终态 mock
            existing = MagicMock(id=1, worker_id=42, status="running")
            only_chain = MagicMock()
            only_chain.first = AsyncMock(return_value=existing)
            m.only = MagicMock(return_value=only_chain)
        else:  # CAS 失败后区分是否存在: filter(run_id=..., worker_id=...).exists() → True
            m.exists = AsyncMock(return_value=True)
        return m

    with patch.object(svc, "_resolve_worker", AsyncMock(return_value=resolved_worker)):
        with patch.object(svc.TaskRun, "filter", side_effect=mock_filter):
            with pytest.raises(PermissionError, match="lease_gen 单调 CAS 失败"):
                await svc.bind_worker_run_lease_generation("worker-1", "run-1", lease_id="lease-old", lease_gen=100)

    # P1-GW-02 后:1 次终态探针 + 1 次 CAS + 1 次 exists = 3
    expected_filter_calls = 3
    assert len(filter_calls) == expected_filter_calls


@pytest.mark.asyncio
async def test_bind_lease_gen_missing_run_raises_specific_permission_error():
    """P1-GW-04:update=0 且 run 不存在 → 明确的"不存在或不属于当前 Worker"。"""
    from antcode_core.application.services.workers import run_ownership_service as svc

    resolved_worker = MagicMock(id=42)

    def mock_filter(*args, **kwargs):
        m = MagicMock()
        if len(args) > 0:  # CAS
            m.update = AsyncMock(return_value=0)
        elif "run_id" in kwargs and "worker_id" not in kwargs:
            # P1-GW-02 终态探针: run 不存在 → first 返回 None
            only_chain = MagicMock()
            only_chain.first = AsyncMock(return_value=None)
            m.only = MagicMock(return_value=only_chain)
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
