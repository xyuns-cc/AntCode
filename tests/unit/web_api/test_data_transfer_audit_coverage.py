"""EXPORT_DATA / IMPORT_DATA / WORKER_RESOURCE_UPDATE 三个枚举值定义齐全却零调用者。

判据与 ``test_mutation_audit_coverage`` 一致：走**真实路由 handler**（不是直接调
``mutation_audit`` 里的辅助函数——那样测不到"路由从不发出审计"这个缺陷本身），并断言
**真表 audit_logs** 里确实多出了对应的行。``audit_service.log`` 不被 mock，被 mock
的只有 handler 下游的业务读写。摘掉任何一处 ``await audit_*``，对应用例立刻查不到行。

导出类用例额外守一条：审计行里不得出现被导出的内容本身——把导出物抄进审计等于把
泄漏面再复制一份。
"""

from __future__ import annotations

import json
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models.audit_log import AuditAction, AuditLog
from antcode_core.domain.models.enums import ExecutionStrategy, ScheduleType
from antcode_core.domain.schemas.repository import ImportProjectsPayload, ProjectImportItem
from antcode_web_api.routes.v1 import crawl, repositories, tasks_transfer, workers_resources
from antcode_web_api.routes.v1 import project as project_routes
from fastapi import HTTPException, status

OPERATOR = SimpleNamespace(user_id=7, username="ops")
SUPER_ADMIN = SimpleNamespace(username="root", is_super_admin=True, is_admin=True)
# 包 __init__ 把同名的单例重新绑定在了这个属性上，import ... as 拿不到模块本身。
RUNTIME_CONTROL = import_module("antcode_core.application.services.runtime.runtime_control_service")


async def _only_log(action: AuditAction) -> AuditLog:
    rows = await AuditLog.filter(action=action).all()
    assert len(rows) == 1, f"{action} 应恰好留下 1 条审计, 实际 {len(rows)}"
    return rows[0]


def _fake_user_model(user: object) -> SimpleNamespace:
    return SimpleNamespace(get_or_none=AsyncMock(return_value=user))


def _import_item(repository_id: str, name: str) -> ProjectImportItem:
    return ProjectImportItem(
        repository_id=repository_id,
        name=name,
        python_version="3.11",
        runtime_scope="private",
        worker_id="worker-1",
        bound_worker_id="worker-1",
        execution_strategy=ExecutionStrategy.FIXED_WORKER,
    )


# --------------------------------------------------------------------------- #
# WORKER_RESOURCE_UPDATE
# --------------------------------------------------------------------------- #


def _worker() -> SimpleNamespace:
    return SimpleNamespace(
        public_id="worker-1",
        name="worker-ui-001",
        resource_limits={"max_concurrent_tasks": 4},
        save=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_worker_resource_update_writes_audit(audit_table, http_request, monkeypatch) -> None:
    worker = _worker()
    monkeypatch.setattr(workers_resources, "User", _fake_user_model(SUPER_ADMIN))
    monkeypatch.setattr(workers_resources.worker_service, "get_worker_by_id", AsyncMock(return_value=worker))
    monkeypatch.setattr(workers_resources, "get_redis_client", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(RUNTIME_CONTROL, "write_control_event", AsyncMock())

    await workers_resources.update_worker_resources(
        "worker-1",
        {"max_concurrent_tasks": 9},
        OPERATOR,
        http_request=http_request,
    )

    row = await _only_log(AuditAction.WORKER_RESOURCE_UPDATE)
    assert row.resource_type == "worker"
    assert row.resource_name == "worker-ui-001"
    # 调整前的限额只在内存里存在过一瞬——resource_limits 是原地 update。
    assert row.old_value == {"max_concurrent_tasks": 4}
    assert row.new_value == {"limits": {"max_concurrent_tasks": 9}, "synced": True}


@pytest.mark.asyncio
async def test_worker_resource_update_rejected_for_regular_admin_writes_no_audit(
    audit_table, http_request, monkeypatch
) -> None:
    regular_admin = SimpleNamespace(username="ops", is_super_admin=False, is_admin=True)
    monkeypatch.setattr(workers_resources, "User", _fake_user_model(regular_admin))

    with pytest.raises(HTTPException) as exc_info:
        await workers_resources.update_worker_resources(
            "worker-1",
            {"max_concurrent_tasks": 9},
            OPERATOR,
            http_request=http_request,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert await AuditLog.filter(action=AuditAction.WORKER_RESOURCE_UPDATE).count() == 0


# --------------------------------------------------------------------------- #
# EXPORT_DATA
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_task_config_export_writes_audit(audit_table, http_request, monkeypatch) -> None:
    task = SimpleNamespace(name="nightly-crawl", public_id="task-1", project_id=1)
    monkeypatch.setattr(tasks_transfer.scheduler_service, "get_task_by_id", AsyncMock(return_value=task))
    monkeypatch.setattr(
        tasks_transfer.Project,
        "get_or_none",
        AsyncMock(return_value=SimpleNamespace(public_id="project-1", name="shop-crawler")),
    )

    await tasks_transfer.export_task_config(
        "task-1",
        "json",
        OPERATOR,
        http_request=http_request,
        task_export_payload=AsyncMock(return_value={"name": "nightly-crawl"}),
    )

    row = await _only_log(AuditAction.EXPORT_DATA)
    assert (row.resource_type, row.resource_id, row.resource_name) == ("task", "task-1", "nightly-crawl")
    assert row.new_value == {"format": "json", "project": "shop-crawler"}


@pytest.mark.asyncio
async def test_export_of_missing_task_writes_no_audit(audit_table, http_request, monkeypatch) -> None:
    monkeypatch.setattr(tasks_transfer.scheduler_service, "get_task_by_id", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc_info:
        await tasks_transfer.export_task_config(
            "task-missing",
            "json",
            OPERATOR,
            http_request=http_request,
            task_export_payload=AsyncMock(),
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert await AuditLog.filter(action=AuditAction.EXPORT_DATA).count() == 0


@pytest.mark.asyncio
async def test_project_export_with_logs_writes_audit_without_copying_payload(
    audit_table, http_request, monkeypatch
) -> None:
    secret = "execution-stdout-secret"
    project = SimpleNamespace(id=1, public_id="project-1", name="shop-crawler")
    monkeypatch.setattr(project_routes.project_service, "get_project_by_id", AsyncMock(return_value=project))
    monkeypatch.setattr(project_routes, "create_project_response", lambda value: SimpleNamespace(model_dump=dict))
    monkeypatch.setattr(project_routes, "_attach_project_detail_info", AsyncMock())
    monkeypatch.setattr(project_routes.user_service, "is_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(project_routes, "_load_export_tasks", AsyncMock(return_value=[{"name": "t1"}]))
    monkeypatch.setattr(
        project_routes,
        "_load_export_executions",
        AsyncMock(return_value=([{"stdout": secret}], ["run-1"])),
    )
    monkeypatch.setattr(project_routes, "load_export_task_logs", AsyncMock(return_value=([], False)))
    monkeypatch.setattr(
        project_routes, "_render_project_export", lambda *a, **k: (secret, "application/json", "f.json")
    )

    await project_routes.export_project_config(
        "project-1",
        project_routes.ProjectExportRequest(format="json", include_tasks=True, include_logs=True),
        1,
        OPERATOR,
        http_request=http_request,
    )

    row = await _only_log(AuditAction.EXPORT_DATA)
    assert (row.resource_type, row.resource_id, row.resource_name) == ("project", "project-1", "shop-crawler")
    assert row.new_value == {"format": "json", "include_tasks": True, "include_logs": True, "task_count": 1}
    assert secret not in json.dumps(row.new_value) + (row.description or "")


@pytest.mark.asyncio
async def test_crawl_batch_export_writes_audit(audit_table, http_request, monkeypatch) -> None:
    batch = SimpleNamespace(public_id="batch-1", name="2026-08 全量", user_id=OPERATOR.user_id)
    monkeypatch.setattr(crawl.crawl_batch_service, "get_batch", AsyncMock(return_value=batch))
    monkeypatch.setattr(crawl, "build_batch_export_response", lambda *a: SimpleNamespace())

    await crawl.export_batch("batch-1", "csv", OPERATOR, http_request=http_request)

    row = await _only_log(AuditAction.EXPORT_DATA)
    assert (row.resource_type, row.resource_id, row.resource_name) == ("crawl_batch", "batch-1", "2026-08 全量")
    assert row.new_value == {"format": "csv"}


@pytest.mark.asyncio
async def test_crawl_batch_export_denied_to_other_user_writes_no_audit(audit_table, http_request, monkeypatch) -> None:
    batch = SimpleNamespace(public_id="batch-1", name="别人的批次", user_id=OPERATOR.user_id + 1)
    monkeypatch.setattr(crawl.crawl_batch_service, "get_batch", AsyncMock(return_value=batch))

    with pytest.raises(HTTPException) as exc_info:
        await crawl.export_batch("batch-1", "csv", OPERATOR, http_request=http_request)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert await AuditLog.filter(action=AuditAction.EXPORT_DATA).count() == 0


# --------------------------------------------------------------------------- #
# IMPORT_DATA
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_task_config_import_writes_audit(audit_table, http_request, monkeypatch) -> None:
    project = SimpleNamespace(id=1, type="code", name="shop-crawler")
    monkeypatch.setattr(tasks_transfer.relation_service, "validate_project_user", AsyncMock(return_value=True))
    monkeypatch.setattr(
        tasks_transfer.relation_service,
        "get_project_with_details",
        AsyncMock(return_value={"project": project}),
    )
    created = SimpleNamespace(name="imported-task", public_id="task-9")
    monkeypatch.setattr(tasks_transfer.scheduler_service, "create_task", AsyncMock(return_value=created))

    await tasks_transfer.import_task_config(
        SimpleNamespace(filename="task.json", read=AsyncMock(return_value=b"{}")),
        "project-1",
        OPERATOR,
        http_request=http_request,
        max_import_bytes=1024,
        create_task_response=lambda value: {"id": value.public_id},
        ensure_specified_worker_access=AsyncMock(),
        generate_unique_task_name=AsyncMock(return_value="imported-task"),
        parse_task_import_payload=lambda raw: {
            "name": "imported-task",
            "command": "echo hi",
            "schedule_type": ScheduleType.INTERVAL,
            "interval_seconds": 60,
        },
        decode_task_import_bytes=lambda raw: raw.decode(),
    )

    row = await _only_log(AuditAction.IMPORT_DATA)
    assert (row.resource_type, row.resource_id, row.resource_name) == ("task", "task-9", "imported-task")
    assert row.new_value == {"source_filename": "task.json", "project": "shop-crawler"}


@pytest.mark.asyncio
async def test_repository_import_writes_audit(audit_table, http_request, monkeypatch) -> None:
    monkeypatch.setattr(repositories, "ensure_worker_access", AsyncMock())
    monkeypatch.setattr(
        repositories.project_source_service,
        "import_projects",
        AsyncMock(return_value=["project-1", "project-2"]),
    )
    payload = ImportProjectsPayload(
        projects=[
            _import_item("repo-1", "p1"),
            _import_item("repo-2", "p2"),
        ]
    )

    await repositories.import_projects_from_repository(
        payload,
        current_user_id=OPERATOR.user_id,
        current_user=OPERATOR,
        http_request=http_request,
    )

    row = await _only_log(AuditAction.IMPORT_DATA)
    assert (row.resource_type, row.resource_name) == ("project", "2 个项目")
    assert row.new_value == {
        "repository_ids": ["repo-1", "repo-2"],
        "created_project_ids": ["project-1", "project-2"],
    }
