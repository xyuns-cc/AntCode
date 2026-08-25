"""POST /api/v1/projects 的字段校验必须回 422，不能回 500。

被这条绑定钉死的缺陷：项目创建是两段式校验——multipart 只能承载字符串，所以
``ProjectCreateFormRequest`` 把 extraction_rules / repository_id 一律声明成
``str | None``（第一段），真正的逐字段类型校验发生在
``build_project_create_request`` 里 ``schema(**request_data)``（第二段）。
第二段在 handler 函数体内，抛的是裸 ``pydantic.ValidationError`` 而不是
``RequestValidationError``，于是 FastAPI 不认，落到 ``general_exception_handler``
被报成 500「服务器内部错误」，逐字段原因只进服务端日志、到不了调用方——用户的
输入错误被谎报成服务端故障。RULE / FILE / CODE 三类共用 ``CREATE_SCHEMA_BY_TYPE``，
三类都有这个形状，所以三类各钉一条。

断言一律走结构化的 ``field``（即 pydantic 的 loc），不拿人类可读文案当契约。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from antcode_core.common.security.auth import get_current_user, get_current_user_id
from antcode_core.domain.schemas.project import (
    ProjectCodeCreateRequest,
    ProjectCreateFormRequest,
    ProjectFileCreateRequest,
    ProjectRuleCreateRequest,
)
from antcode_web_api.exceptions import (
    BusinessException,
    business_exception_handler,
    general_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from antcode_web_api.routes.v1.project import project_router
from antcode_web_api.routes.v1.project_create_request import build_project_create_request
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

HTTP_UNPROCESSABLE_CONTENT = 422

VALID_RULE = '[{"desc":"title","type":"css","expr":"h1"}]'


def _build_app() -> FastAPI:
    """挂真实路由 + 真实异常处理器，只把鉴权换成测试桩。

    鉴权替身不是"mock 成功路径"：下面每条用例都在业务逻辑之前就被校验拦下，
    真实的 create_project 服务根本不会被调用；换掉鉴权只是为了让请求能走到校验。
    """
    app = FastAPI()
    app.add_exception_handler(BusinessException, business_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)
    app.include_router(project_router, prefix="/api/v1/projects")
    app.dependency_overrides[get_current_user_id] = lambda: 1
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=1, is_admin=False)
    return app


@pytest.fixture
def client() -> TestClient:
    # raise_server_exceptions=False：未捕获异常要真的走 general_exception_handler
    # 变成 500 响应，否则 TestClient 直接把异常抛回测试，"500"这一面就断言不到。
    return TestClient(_build_app(), raise_server_exceptions=False)


def _post(client: TestClient, form: dict[str, Any]):
    return client.post("/api/v1/projects", data=form)


def _error_fields(response) -> list[str]:
    payload = response.json()
    assert payload["data"] is not None, f"422 必须带逐字段原因，实得 {payload}"
    return [error["field"] for error in payload["data"]["errors"]]


# --- 失败臂：第二段校验才能判定的错误，三类各一条 ---------------------------


def test_rule_project_reports_bad_extraction_rule_field_as_422(client: TestClient) -> None:
    """规则数组里字段名写错（desc 写成 description）：第一段只见字符串，判不出来。"""
    response = _post(
        client,
        {
            "name": "rule-typo-inner",
            "type": "rule",
            "runtime_scope": "shared",
            "target_url": "https://example.com",
            "extraction_rules": '[{"description":"title","type":"css","expr":"h1"}]',
        },
    )

    assert response.status_code == HTTP_UNPROCESSABLE_CONTENT
    assert _error_fields(response) == ["body.extraction_rules"]
    # 笼统的 4xx 不算修好：响应必须指到出错的规则字段名上
    assert "desc" in response.json()["data"]["errors"][0]["message"]


def test_rule_project_reports_bad_request_method_as_422(client: TestClient) -> None:
    """表单把 request_method 声明成 str，枚举收窄只发生在第二段。"""
    response = _post(
        client,
        {
            "name": "rule-bad-method",
            "type": "rule",
            "runtime_scope": "shared",
            "target_url": "https://example.com",
            "extraction_rules": VALID_RULE,
            "request_method": "FETCH",
        },
    )

    assert response.status_code == HTTP_UNPROCESSABLE_CONTENT
    assert _error_fields(response) == ["body.request_method"]


def test_file_project_reports_missing_repository_id_as_422(client: TestClient) -> None:
    """repository_id 在表单里可空、在 FILE 的 CreateRequest 里必填。"""
    response = _post(
        client,
        {
            "name": "file-missing-repo",
            "type": "file",
            "runtime_scope": "shared",
            "subdir": "app",
        },
    )

    assert response.status_code == HTTP_UNPROCESSABLE_CONTENT
    assert _error_fields(response) == ["body.repository_id"]


def test_code_project_reports_missing_repository_fields_as_422(client: TestClient) -> None:
    """CODE 类同样必填 repository_id / subdir，缺两项就该逐项报两条。"""
    response = _post(
        client,
        {
            "name": "code-missing-repo",
            "type": "code",
            "runtime_scope": "shared",
        },
    )

    assert response.status_code == HTTP_UNPROCESSABLE_CONTENT
    assert _error_fields(response) == ["body.repository_id", "body.subdir"]


# --- 控制组：证明没把正常路径挡死，也没退化第一段 ---------------------------


@pytest.mark.parametrize(
    ("form_kwargs", "expected_schema"),
    [
        (
            {
                "name": "rule-ok",
                "type": "rule",
                "runtime_scope": "shared",
                "target_url": "https://example.com",
                "extraction_rules": VALID_RULE,
            },
            ProjectRuleCreateRequest,
        ),
        (
            {
                "name": "file-ok",
                "type": "file",
                "runtime_scope": "shared",
                "worker_id": "worker-1",
                "python_version": "3.12",
                "repository_id": "repo-1",
                "subdir": "app",
            },
            ProjectFileCreateRequest,
        ),
        (
            {
                "name": "code-ok",
                "type": "code",
                "runtime_scope": "shared",
                "worker_id": "worker-1",
                "python_version": "3.12",
                "repository_id": "repo-1",
                "subdir": "app",
            },
            ProjectCodeCreateRequest,
        ),
    ],
    ids=["rule", "file", "code"],
)
def test_valid_form_still_builds_typed_create_request(form_kwargs: dict, expected_schema: type) -> None:
    """控制组（非证伪项）：合法输入仍然照常组装出对应类型的 CreateRequest。"""
    request = build_project_create_request(ProjectCreateFormRequest(**form_kwargs))

    assert isinstance(request, expected_schema)


def test_unknown_top_level_form_field_still_rejected_by_first_stage(client: TestClient) -> None:
    """控制组（非证伪项）：第一段的 extra=forbid 本来就回 422，不能被改坏。"""
    response = _post(
        client,
        {
            "name": "rule-typo-top",
            "type": "rule",
            "runtime_scope": "shared",
            "target_url": "https://example.com",
            "extractionrules": "[]",
        },
    )

    assert response.status_code == HTTP_UNPROCESSABLE_CONTENT
    assert _error_fields(response) == ["body.extractionrules"]
