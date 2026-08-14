from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.scheduler import spider_dispatcher


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["js_click", "infinite_scroll", "javascript", "ajax"])
async def test_rule_dispatch_forwards_limits_and_requires_render(monkeypatch, method: str) -> None:
    dispatch_task = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            task_id="run-1",
            worker_id="worker-1",
            worker_name="Worker 1",
            message="accepted",
        )
    )
    monkeypatch.setattr(spider_dispatcher.worker_task_dispatcher, "dispatch_task", dispatch_task)
    detail = SimpleNamespace(
        region="cn-east",
        require_render=False,
        to_dispatch_dict=lambda: {
            "engine": "requests",
            "pagination_config": {"method": method},
        },
    )

    result = await spider_dispatcher.spider_task_dispatcher.submit_rule_task(
        SimpleNamespace(public_id="project-1", env_location=None),
        detail,
        "run-1",
        worker_id="worker-1",
        timeout=45,
        priority=7,
    )

    assert result["success"] is True
    kwargs = dispatch_task.await_args.kwargs
    assert kwargs["timeout"] == 45
    assert kwargs["priority"] == 7
    assert kwargs["region"] == "cn-east"
    assert kwargs["require_render"] is True


@pytest.mark.asyncio
async def test_rule_dispatch_preserves_explicit_render_requirement(monkeypatch) -> None:
    dispatch_task = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            task_id="run-1",
            worker_id="worker-1",
            worker_name="Worker 1",
            message="accepted",
        )
    )
    monkeypatch.setattr(spider_dispatcher.worker_task_dispatcher, "dispatch_task", dispatch_task)
    detail = SimpleNamespace(
        region=None,
        require_render=True,
        to_dispatch_dict=lambda: {"engine": "requests"},
    )

    await spider_dispatcher.spider_task_dispatcher.submit_rule_task(
        SimpleNamespace(public_id="project-1", env_location=None),
        detail,
        "run-1",
    )

    assert dispatch_task.await_args.kwargs["require_render"] is True
