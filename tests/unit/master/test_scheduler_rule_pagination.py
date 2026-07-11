from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_master.control import scheduler_loop


@pytest.mark.asyncio
async def test_rule_pagination_fails_when_no_page_is_submitted(monkeypatch):
    service = scheduler_loop.SchedulerService()
    service._log_execution = AsyncMock()
    monkeypatch.setattr(
        scheduler_loop.spider_task_dispatcher,
        "submit_rule_task",
        AsyncMock(return_value={"success": False, "error": "dispatch failed"}),
    )
    rule_detail = SimpleNamespace(
        pagination_config={"method": "url_pattern", "start_page": 1, "max_pages": 2},
        target_url="https://example.com/page/{}",
    )

    result = await service._execute_rule_task(
        SimpleNamespace(id=1, name="task", execution_params={}),
        SimpleNamespace(public_id="project-1"),
        rule_detail,
        SimpleNamespace(run_id="run-1"),
    )

    assert result["success"] is False
    assert "dispatch failed" in result["error"]


@pytest.mark.asyncio
async def test_rule_pagination_fails_when_any_page_dispatch_fails(monkeypatch):
    service = scheduler_loop.SchedulerService()
    service._log_execution = AsyncMock()
    submit_rule_task = AsyncMock(
        side_effect=[
            {"success": True, "task_id": "task-page-1", "worker_name": "worker-1"},
            {"success": False, "error": "page 2 dispatch failed"},
        ]
    )
    monkeypatch.setattr(
        scheduler_loop.spider_task_dispatcher,
        "submit_rule_task",
        submit_rule_task,
    )
    rule_detail = SimpleNamespace(
        pagination_config={"method": "url_pattern", "start_page": 1, "max_pages": 2},
        target_url="https://example.com/page/{}",
    )

    result = await service._execute_rule_task(
        SimpleNamespace(id=1, name="task", execution_params={}),
        SimpleNamespace(public_id="project-1"),
        rule_detail,
        SimpleNamespace(run_id="run-1"),
    )

    assert result["success"] is False
    assert "page 2 dispatch failed" in result["error"]
    assert result["task_ids"] == ["task-page-1"]
