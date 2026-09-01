"""项目创建时的 Worker 绑定必须真的影响派发。

回归目标（第五棒 P0）：``_bind_worker_runtime`` 只写 ``projects.worker_id``
（public_id 字符串）而从不写 ``projects.bound_worker_id``（内部整型），
而默认 ``prefer`` 策略的 ``_resolve_prefer_bound`` 唯一判据就是 ``bound_worker_id``。
集群一有第二个 Worker，任务就被派到没有该运行时环境的节点，100% 失败。

这里刻意**不**只断言「字段被写了」——那种测试挡不住「写了但调度不认」这一族缺陷。
下面第二个用例把绑定结果原样喂进真实的 ExecutionResolver，断言派发落点，
并断言自动选节点这条路**根本没被走到**。
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.projects.project_runtime_binding import ProjectRuntimeBindingMixin
from antcode_core.application.services.scheduler import execution_resolver as resolver_module
from antcode_core.application.services.scheduler.execution_resolver import ExecutionResolver
from antcode_core.domain.models.enums import ExecutionStrategy, RuntimeKind, RuntimeScope

RUNTIME_WORKER_INTERNAL_ID = 1
OTHER_WORKER_INTERNAL_ID = 2
RUNTIME_WORKER_PUBLIC_ID = "ac696e6847ea4dc298a7dbca78bed0d2"
ENV_NAME = "shared-py311"


class _FakeProject:
    """只提供 _bind_worker_runtime 真正用到的东西：属性赋值 + 一次 save。"""

    def __init__(self) -> None:
        self.name = "proj"
        self.saved = 0

    async def save(self, using_db=None) -> None:
        self.saved += 1


def _runtime_payload() -> dict:
    return {
        "worker_id": RUNTIME_WORKER_PUBLIC_ID,
        "env_name": ENV_NAME,
        "python_version": "3.11.11",
        "scope": RuntimeScope.SHARED,
        "kind": RuntimeKind.PYTHON,
    }


async def _bind() -> _FakeProject:
    project = _FakeProject()
    worker = SimpleNamespace(id=RUNTIME_WORKER_INTERNAL_ID, name="worker-ui-001", public_id=RUNTIME_WORKER_PUBLIC_ID)
    await ProjectRuntimeBindingMixin._bind_worker_runtime(project, worker, _runtime_payload(), conn=None)
    return project


@pytest.mark.asyncio
async def test_binding_writes_both_runtime_location_and_dispatch_binding() -> None:
    project = await _bind()

    assert project.saved == 1
    # 运行时位置（public_id）与调度绑定（内部 id）是两列，必须同时写。
    assert project.worker_id == RUNTIME_WORKER_PUBLIC_ID
    assert project.worker_env_name == ENV_NAME
    assert project.bound_worker_id == RUNTIME_WORKER_INTERNAL_ID


@pytest.mark.asyncio
async def test_prefer_strategy_dispatches_to_the_worker_holding_the_runtime(monkeypatch) -> None:
    """绑定结果原样进真实 resolver：必须落在持有环境的节点，且不得走自动选择。"""
    bound = await _bind()
    project = SimpleNamespace(
        bound_worker_id=bound.bound_worker_id,
        worker_id=bound.worker_id,
        execution_strategy=ExecutionStrategy.PREFER_BOUND,
    )
    runtime_worker = SimpleNamespace(id=RUNTIME_WORKER_INTERNAL_ID, name="worker-ui-001")
    resolver = ExecutionResolver()
    monkeypatch.setattr(
        resolver_module.Worker,
        "get_or_none",
        AsyncMock(return_value=runtime_worker),
    )
    monkeypatch.setattr(resolver, "_preferred_worker_is_usable", AsyncMock(return_value=True))
    auto_select = AsyncMock(return_value=SimpleNamespace(id=OTHER_WORKER_INTERNAL_ID, name="worker-ui-002"))
    monkeypatch.setattr(resolver, "_resolve_auto_select", auto_select)

    selected = await resolver._resolve_prefer_bound(project, task=SimpleNamespace(user_id=1))

    assert selected is runtime_worker
    # 缺陷原形态就是「悄悄落回自动选择」，所以这条断言比落点断言更关键。
    auto_select.assert_not_awaited()


@pytest.mark.asyncio
async def test_prefer_strategy_falls_back_to_auto_select_when_nothing_is_bound(monkeypatch) -> None:
    """反向对照：真的没绑定时才允许自动选择，证明上一个用例不是恒真。"""
    project = SimpleNamespace(bound_worker_id=None, execution_strategy=ExecutionStrategy.PREFER_BOUND)
    resolver = ExecutionResolver()
    picked = SimpleNamespace(id=OTHER_WORKER_INTERNAL_ID, name="worker-ui-002")
    auto_select = AsyncMock(return_value=picked)
    monkeypatch.setattr(resolver, "_resolve_auto_select", auto_select)

    assert await resolver._resolve_prefer_bound(project, task=SimpleNamespace(user_id=1)) is picked
    auto_select.assert_awaited_once()


def test_binding_has_no_silent_read_side_fallback() -> None:
    """prefer 策略不得偷偷改读 worker_id 兜底——那样会让显式解绑再也解不掉。"""
    resolver_source = Path(
        "packages/antcode_core/src/antcode_core/application/services/scheduler/execution_resolver.py"
    ).read_text(encoding="utf-8")
    prefer_body = resolver_source.split("async def _resolve_prefer_bound")[1].split("async def ")[0]

    assert "bound_worker_id" in prefer_body
    assert "worker_env_name" not in prefer_body
    assert "project.worker_id" not in prefer_body
