import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models import Project
from antcode_core.domain.models.enums import ExecutionStrategy, ScheduleType
from antcode_core.domain.schemas.task import TaskCreateRequest
from antcode_web_api.routes.v1 import project as project_routes
from antcode_web_api.routes.v1 import tasks as task_routes
from fastapi import Request


def _sensitive_task():
    return SimpleNamespace(
        name="exportable-task",
        description="safe description",
        schedule_type=ScheduleType.INTERVAL,
        cron_expression=None,
        interval_seconds=60,
        scheduled_time=None,
        max_instances=1,
        timeout_seconds=120,
        retry_count=2,
        retry_delay=30,
        execution_params={"token": "execution-secret", "page": 3},
        environment_vars={"DATABASE_URL": "postgresql://user:password@db/app"},
        is_active=True,
        execution_strategy=ExecutionStrategy.AUTO_SELECT,
        public_id="task-other-user",
        project_id=1,
        user_id=7,
    )


def _assert_safe_and_reimportable(payload: dict) -> None:
    encoded = json.dumps(payload, default=str)
    assert "execution_params" not in payload
    assert "environment_vars" not in payload
    assert "execution-secret" not in encoded
    assert "password@db" not in encoded
    imported = TaskCreateRequest(**payload)
    assert imported.name == "exportable-task"
    assert imported.execution_params is None
    assert imported.environment_vars is None


@pytest.mark.asyncio
async def test_task_and_project_exports_omit_decrypted_runtime_secrets() -> None:
    task = _sensitive_task()
    project = SimpleNamespace(public_id="project-1")

    task_payload = await task_routes._task_export_payload(task, project)
    project_payload = project_routes._task_export_payload(task, project.public_id)

    _assert_safe_and_reimportable(task_payload)
    _assert_safe_and_reimportable(project_payload)


@pytest.mark.asyncio
async def test_admin_export_of_other_users_task_stays_secret_free(audit_table, monkeypatch) -> None:
    task = _sensitive_task()
    project = SimpleNamespace(id=1, public_id="project-1", name="exportable-project")
    get_task = AsyncMock(return_value=task)
    monkeypatch.setattr(task_routes.scheduler_service, "get_task_by_id", get_task)
    monkeypatch.setattr(Project, "get_or_none", AsyncMock(return_value=project))

    response = await task_routes.export_task_config(
        "task-other-user",
        format="json",
        current_user=SimpleNamespace(user_id=99, username="admin", is_admin=True),
        http_request=Request({"type": "http", "client": ("127.0.0.1", 1234)}),
    )
    body = b"".join([chunk async for chunk in response.body_iterator])
    exported = json.loads(body)

    get_task.assert_awaited_once_with("task-other-user", 99)
    _assert_safe_and_reimportable(exported["task"])
