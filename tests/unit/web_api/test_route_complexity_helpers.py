from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models.enums import TaskStatus
from antcode_web_api.routes.v1 import alert_config_store, monitoring, project, runs, tasks
from fastapi import HTTPException


def test_alert_config_parser_preserves_json_default_on_invalid_value():
    config = {"email_config": {"smtp_host": "old"}}

    parsed = alert_config_store._apply_alert_config(config, "email_config", "not-json")

    assert parsed == {"email_config": {}}
    assert config == {"email_config": {"smtp_host": "old"}}


def test_worker_history_item_converts_decimal_and_removes_metadata():
    item = monitoring._worker_history_item(
        {
            "id": 1,
            "worker_id": "worker-1",
            "created_at": None,
            "status": "online",
            "timestamp": datetime(2026, 7, 11, tzinfo=UTC),
            "cpu_percent": Decimal("12.5"),
        }
    )

    assert item.cpu_percent == 12.5
    assert item.memory_percent == 0.0


def test_project_validation_collects_required_and_rule_errors():
    payload = project.ProjectValidateRequest(type="rule")
    errors = project._required_project_errors(payload)

    project._validate_project_type(payload, errors)

    assert errors == [
        "项目名称不能为空",
        "运行时作用域不能为空",
        "Python 版本不能为空",
        "规则项目必须提供 target_url",
        "规则项目必须提供 extraction_rules",
    ]


def test_project_export_date_reports_field_name():
    with pytest.raises(HTTPException, match="date_range.start 格式错误") as exc_info:
        project._parse_export_date("invalid", "start")

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_batch_action_dispatches_enable(monkeypatch):
    update_task = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(tasks.scheduler_service, "update_task", update_task)

    assert await tasks._operate_task("task-1", "enable", 7) is True
    request = update_task.await_args.args[1]
    assert request.is_active is True


def test_unassigned_pending_execution_is_cancellable_without_worker():
    execution = SimpleNamespace(worker_id=None, status=TaskStatus.PENDING)

    assert runs._is_unassigned_execution(execution) is True
