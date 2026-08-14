from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.scheduler import execution_constraints
from antcode_core.application.services.scheduler import execution_resolver as resolver_module
from antcode_core.application.services.scheduler.execution_resolver import ExecutionResolver
from antcode_core.application.services.scheduler.rule_dispatch_constraints import RuleDispatchConstraints
from antcode_core.application.services.workers import worker_service
from antcode_core.common.exceptions import WorkerUnavailableError
from antcode_core.domain.models.enums import ExecutionStrategy


class _UserQuery:
    def __init__(self, is_admin: bool):
        self._is_admin = is_admin

    async def exists(self) -> bool:
        return self._is_admin


@pytest.mark.asyncio
async def test_fixed_worker_requires_task_owner_use_permission(monkeypatch) -> None:
    resolver = ExecutionResolver()
    worker = SimpleNamespace(id=3)
    task = SimpleNamespace(name="task", user_id=7, execution_strategy=ExecutionStrategy.FIXED_WORKER)
    project = SimpleNamespace(execution_strategy=None)
    monkeypatch.setattr(resolver, "_resolve_fixed_worker", AsyncMock(return_value=worker))
    monkeypatch.setattr(resolver_module.User, "filter", MagicMock(return_value=_UserQuery(False)))
    monkeypatch.setattr(worker_service, "check_user_worker_permission", AsyncMock(return_value=False))

    with pytest.raises(WorkerUnavailableError, match="use 权限"):
        await resolver.resolve_execution_worker(task, project)


@pytest.mark.asyncio
async def test_auto_selection_only_receives_usable_workers(monkeypatch) -> None:
    resolver = ExecutionResolver()
    task = SimpleNamespace(user_id=7)
    allowed = [SimpleNamespace(id=3)]
    monkeypatch.setattr(resolver_module.User, "filter", MagicMock(return_value=_UserQuery(False)))
    get_workers = AsyncMock(return_value=allowed)
    monkeypatch.setattr(worker_service, "get_user_workers", get_workers)

    assert await resolver._usable_workers(task) == allowed
    get_workers.assert_awaited_once_with(7, is_admin=False, required_permission="use")


@pytest.mark.asyncio
async def test_auto_selection_filters_region_and_render_capability(monkeypatch) -> None:
    resolver = ExecutionResolver()
    west = SimpleNamespace(id=1, name="west", region="cn-west")
    east = SimpleNamespace(id=2, name="east", region="cn-east")
    resolver._usable_workers = AsyncMock(return_value=[west, east])
    select = AsyncMock(return_value=east)

    from antcode_core.application.services import workers as workers_package

    monkeypatch.setattr(workers_package.worker_load_balancer, "select_best_worker", select)
    constraints = RuleDispatchConstraints(region="cn-east", require_render=True)
    selected = await resolver._resolve_auto_select(object(), task=object(), constraints=constraints)

    assert selected is east
    select.assert_awaited_once_with(
        workers=[east],
        exclude_workers=None,
        region="cn-east",
        require_render=True,
        require_task_type=None,
    )


@pytest.mark.asyncio
async def test_fixed_worker_rejects_region_mismatch(monkeypatch) -> None:
    resolver = ExecutionResolver()
    worker = SimpleNamespace(id=3, name="west", region="cn-west")
    task = SimpleNamespace(name="task", user_id=7, execution_strategy=ExecutionStrategy.FIXED_WORKER)
    project = SimpleNamespace(execution_strategy=None)
    monkeypatch.setattr(resolver, "_resolve_fixed_worker", AsyncMock(return_value=worker))
    monkeypatch.setattr(resolver, "_require_worker_use_access", AsyncMock())

    with pytest.raises(WorkerUnavailableError, match="区域不匹配"):
        await resolver.resolve_execution_worker(
            task,
            project,
            constraints=RuleDispatchConstraints(region="cn-east", require_render=False),
        )


@pytest.mark.asyncio
async def test_specified_worker_rejects_missing_render_capability(monkeypatch) -> None:
    resolver = ExecutionResolver()
    worker = SimpleNamespace(id=3, name="plain", region="cn-east")
    task = SimpleNamespace(name="task", user_id=7, execution_strategy=ExecutionStrategy.SPECIFIED)
    project = SimpleNamespace(execution_strategy=None)
    monkeypatch.setattr(resolver, "_resolve_specified_worker", AsyncMock(return_value=worker))
    monkeypatch.setattr(resolver, "_require_worker_use_access", AsyncMock())
    capabilities = AsyncMock(return_value={worker.id: {"playwright": {"enabled": False}}})
    monkeypatch.setattr(execution_constraints, "resolve_selection_capabilities", capabilities)

    with pytest.raises(WorkerUnavailableError, match="不具备渲染能力"):
        await resolver.resolve_execution_worker(
            task,
            project,
            constraints=RuleDispatchConstraints(region=None, require_render=True),
        )

    capabilities.assert_awaited_once_with([worker], True, None)


@pytest.mark.asyncio
async def test_rule_worker_requires_rule_plugin(monkeypatch) -> None:
    resolver = ExecutionResolver()
    worker = SimpleNamespace(id=3, name="code-only", region="cn-east")
    task = SimpleNamespace(name="task", user_id=7, execution_strategy=ExecutionStrategy.SPECIFIED)
    project = SimpleNamespace(type="rule", execution_strategy=None)
    monkeypatch.setattr(resolver, "_resolve_specified_worker", AsyncMock(return_value=worker))
    monkeypatch.setattr(resolver, "_require_worker_use_access", AsyncMock())
    capabilities = AsyncMock(return_value={worker.id: {"task_types": ["code"]}})
    monkeypatch.setattr(execution_constraints, "resolve_selection_capabilities", capabilities)

    with pytest.raises(WorkerUnavailableError, match="不支持任务类型 rule"):
        await resolver.resolve_execution_worker(task, project)

    capabilities.assert_awaited_once_with([worker], False, "rule")
