"""Scheduled Rule tasks must select a Worker with persisted routing constraints."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models.enums import ProjectType
from antcode_master.control import scheduler_dispatch


@pytest.mark.asyncio
async def test_rule_constraints_apply_before_final_worker_selection(monkeypatch) -> None:
    service = SimpleNamespace(
        _log_execution=AsyncMock(),
        _execute_rule_task=AsyncMock(return_value={"success": True}),
    )
    task = SimpleNamespace(name="rule-task")
    project = SimpleNamespace(type=ProjectType.RULE)
    detail = SimpleNamespace(
        region="cn-east",
        require_render=False,
        to_dispatch_dict=lambda: {"engine": "playwright"},
    )
    execution = SimpleNamespace(run_id="rule-run", scheduler_fencing_token=7)
    worker = SimpleNamespace(name="render-east")
    resolve = AsyncMock(return_value=(worker, "auto_select"))
    monkeypatch.setattr(scheduler_dispatch.execution_resolver, "resolve_execution_worker", resolve)
    claim = AsyncMock(return_value=True)

    result = await scheduler_dispatch.dispatch_prepared_run(
        service,
        (task, project, detail, execution, "rule-run", datetime.now(UTC), claim),
    )

    assert result == {"success": True}
    constraints = resolve.await_args.kwargs["constraints"]
    assert constraints.region == "cn-east"
    assert constraints.require_render is True
    service._execute_rule_task.assert_awaited_once_with(
        task,
        project,
        detail,
        execution,
        target_worker=worker,
    )
