"""批量入口必须把逐项失败原因带进响应体。

三个入口（``POST /tasks/batch-delete``、``POST /tasks/batch``、
``POST /projects/batch-delete``）过去把 ``RunSettlementPendingError`` 这类域内
拒绝一并吞进 ``except Exception``，只回一串 id：运维看到"某几个失败了"，却不知道
该去取消哪条在途执行。同一时刻单条删除返回的是点名在线 Worker 的 409 文案——
差距就是这个缺陷。

测试走**真实路由**：挂 ``v1_router``，只替换鉴权依赖与服务层，被测 handler 本身
不打桩。断言拒绝原文逐字出现在 ``data["failures"]`` 里，并钉住三件不该退化的事：
旧字段仍在、未分类异常不外泄内部细节、结算状态不可得仍是整批 503。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from antcode_core.application.services.projects.project_delete_scope import ACTIVE_RUN_REJECTION
from antcode_core.application.services.projects.project_service import project_service
from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.application.services.workers.run_settlement_guard import (
    RunSettlementGuardUnavailable,
    RunSettlementPendingError,
)
from antcode_core.common.security.auth import get_current_user
from antcode_web_api.routes.v1 import v1_router
from antcode_web_api.routes.v1.project import PROJECT_DELETE_REASONS
from antcode_web_api.routes.v1.tasks_batch import DELETE_REASONS, OPERATE_REASONS
from fastapi import FastAPI
from fastapi.testclient import TestClient

CONTRACT = json.loads(Path("contracts/http/batch_delete_failures.json").read_text(encoding="utf-8"))

USER_ID = 7
OK_TASK = "task-ok"
BLOCKED_TASK = "task-blocked"
OK_PROJECT = "project-ok"
BLOCKED_PROJECT = "project-blocked"
HTTP_OK = 200
HTTP_CONFLICT = 409
HTTP_UNAVAILABLE = 503

# 单条删除守卫在 33944ff 之后会点名仍在线的 Worker；批量必须原样转达同一句话。
PENDING_REASON = "执行 run-9 仍由在线 Worker worker-a 持有，请等待其上报结算结果后再删除"
INTERNAL_DETAIL = "postgresql://svc:secret@db/private-table"
STORE_UNAVAILABLE = "执行结算状态服务不可用"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(v1_router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=USER_ID)
    with TestClient(app) as test_client:
        yield test_client


def _payload(response) -> dict:
    assert response.status_code == HTTP_OK, response.text
    return response.json()["data"]


def _rejecting(blocked_id: str, error: type[Exception] | Exception, message: str):
    """只对 ``blocked_id`` 抛出指定异常，其余 id 正常成功。"""

    async def _operate(item_id, _user_id):
        if item_id != blocked_id:
            return True
        raise error(message) if isinstance(error, type) else error

    return _operate


@pytest.mark.parametrize(
    "post_batch",
    [
        pytest.param(
            lambda client: client.post("/tasks/batch-delete", json={"task_ids": [OK_TASK, BLOCKED_TASK]}),
            id="batch-delete",
        ),
        pytest.param(
            lambda client: client.post(
                "/tasks/batch",
                json={"task_ids": [OK_TASK, BLOCKED_TASK], "action": "delete"},
            ),
            id="batch-action",
        ),
    ],
)
def test_task_batch_reports_the_settlement_rejection_per_item(monkeypatch, client, post_batch) -> None:
    monkeypatch.setattr(
        scheduler_service,
        "delete_task",
        _rejecting(BLOCKED_TASK, RunSettlementPendingError, PENDING_REASON),
    )

    data = _payload(post_batch(client))

    assert data["success_count"] == 1
    assert data["failed_count"] == 1
    assert data["failed_ids"] == [BLOCKED_TASK]
    assert data["failures"] == [{"id": BLOCKED_TASK, "reason": PENDING_REASON}]


def test_project_batch_reports_the_in_flight_run_rejection_per_item(monkeypatch, client) -> None:
    monkeypatch.setattr(
        project_service,
        "delete_project",
        _rejecting(BLOCKED_PROJECT, ValueError, ACTIVE_RUN_REJECTION),
    )

    requested = [OK_PROJECT, BLOCKED_PROJECT]
    data = _payload(client.post("/projects/batch-delete", json={"project_ids": requested}))

    assert data["total"] == len(requested)
    assert data["success_count"] == 1
    assert data["failed_projects"] == [BLOCKED_PROJECT]
    assert data["failures"] == [{"id": BLOCKED_PROJECT, "reason": ACTIVE_RUN_REJECTION}]


def test_batch_reason_is_identical_to_the_single_delete_conflict_detail(monkeypatch, client) -> None:
    """同一个守卫拒绝：单条走 409 detail，批量走逐项 reason，必须是同一句话。"""

    async def _lookup(_task_id, _user_id):
        return SimpleNamespace(name="doomed")

    monkeypatch.setattr(scheduler_service, "get_task_by_id", _lookup)
    monkeypatch.setattr(
        scheduler_service,
        "delete_task",
        _rejecting(BLOCKED_TASK, RunSettlementPendingError, PENDING_REASON),
    )

    single = client.delete(f"/tasks/{BLOCKED_TASK}")
    batch = client.post("/tasks/batch-delete", json={"task_ids": [BLOCKED_TASK]})

    assert single.status_code == HTTP_CONFLICT
    assert _payload(batch)["failures"] == [{"id": BLOCKED_TASK, "reason": single.json()["detail"]}]


def test_unclassified_failure_reports_a_fixed_reason_without_leaking_internals(monkeypatch, client) -> None:
    monkeypatch.setattr(
        scheduler_service,
        "delete_task",
        _rejecting(BLOCKED_TASK, RuntimeError, INTERNAL_DETAIL),
    )

    data = _payload(client.post("/tasks/batch-delete", json={"task_ids": [BLOCKED_TASK]}))

    assert data["failures"] == [{"id": BLOCKED_TASK, "reason": DELETE_REASONS.unexpected}]
    assert INTERNAL_DETAIL not in str(data)


def test_falsy_service_result_reports_the_missing_reason(monkeypatch, client) -> None:
    async def _refuse(_item_id, _user_id):
        return False

    monkeypatch.setattr(scheduler_service, "delete_task", _refuse)
    monkeypatch.setattr(project_service, "delete_project", _refuse)

    tasks = _payload(client.post("/tasks/batch-delete", json={"task_ids": [BLOCKED_TASK]}))
    projects = _payload(client.post("/projects/batch-delete", json={"project_ids": [BLOCKED_PROJECT]}))

    assert tasks["failures"] == [{"id": BLOCKED_TASK, "reason": DELETE_REASONS.missing}]
    assert projects["failures"] == [{"id": BLOCKED_PROJECT, "reason": PROJECT_DELETE_REASONS.missing}]


def test_unsupported_batch_action_no_longer_disappears_into_failed_ids(client) -> None:
    """未知 action 由 ``_operate_task`` 抛 400，它的 detail 同样是"原因"，不能吞。"""
    data = _payload(client.post("/tasks/batch", json={"task_ids": [OK_TASK], "action": "obliterate"}))

    assert data["failures"] == [{"id": OK_TASK, "reason": "不支持的操作类型"}]
    assert OPERATE_REASONS.unexpected not in str(data)


def test_response_shape_matches_the_published_contract(monkeypatch, client) -> None:
    """键集由 contracts/http/batch_delete_failures.json 单点定义，前端按同一份造响应。

    新增 ``failures`` 是纯增量：``legacy_failed_ids_key`` 指向的旧字段仍在键集里，
    只读旧字段的调用方不受影响。谁把旧字段删掉或把新字段改名，这里立刻红。
    """
    monkeypatch.setattr(scheduler_service, "delete_task", _rejecting(BLOCKED_TASK, ValueError, PENDING_REASON))
    monkeypatch.setattr(project_service, "delete_project", _rejecting(BLOCKED_PROJECT, ValueError, PENDING_REASON))
    bodies = {
        "/tasks/batch-delete": {"task_ids": [BLOCKED_TASK]},
        "/tasks/batch": {"task_ids": [BLOCKED_TASK], "action": "delete"},
        "/projects/batch-delete": {"project_ids": [BLOCKED_PROJECT]},
    }

    for endpoint in CONTRACT["endpoints"]:
        data = _payload(client.post(endpoint["path"], json=bodies[endpoint["path"]]))
        assert set(data) == set(endpoint["data_keys"]), endpoint["name"]
        assert endpoint["legacy_failed_ids_key"] in data, endpoint["name"]
        assert [set(item) for item in data["failures"]] == [set(CONTRACT["failure_item_keys"])]


@dataclass(frozen=True)
class _BatchEndpoint:
    path: str
    body: dict
    service: object
    attribute: str
    blocked_id: str


UNAVAILABLE_CASES = [
    pytest.param(
        _BatchEndpoint(
            "/tasks/batch-delete", {"task_ids": [BLOCKED_TASK]}, scheduler_service, "delete_task", BLOCKED_TASK
        ),
        id="tasks",
    ),
    pytest.param(
        _BatchEndpoint(
            "/projects/batch-delete",
            {"project_ids": [BLOCKED_PROJECT]},
            project_service,
            "delete_project",
            BLOCKED_PROJECT,
        ),
        id="projects",
    ),
]


@pytest.mark.parametrize("endpoint", UNAVAILABLE_CASES)
def test_guard_unavailable_still_fails_the_whole_batch(monkeypatch, client, endpoint) -> None:
    """结算状态不可得时无法逐项定性，整批 503——不得降级成一条 failures。"""
    monkeypatch.setattr(
        endpoint.service,
        endpoint.attribute,
        _rejecting(endpoint.blocked_id, RunSettlementGuardUnavailable, STORE_UNAVAILABLE),
    )

    response = client.post(endpoint.path, json=endpoint.body)

    assert response.status_code == HTTP_UNAVAILABLE
    assert response.json()["detail"] == STORE_UNAVAILABLE
