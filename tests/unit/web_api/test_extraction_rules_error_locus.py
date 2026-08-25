"""extraction_rules 的校验错误必须指到「第几条的哪个字段」，三条路由同一形状。

同一个字段有三个入口，各自一份 mode="before" 解析器，曾经三种行为：

* ``POST /api/v1/projects``（``ProjectRuleCreateRequest``）——传 list 时 loc 正常，
  传 JSON 字符串时自己 new ExtractionRule 并 ``except Exception`` 转
  ``ValueError(str(e))``，把嵌套 ValidationError 压成文本，loc 退化到
  ``extraction_rules``。而 multipart 只能承载字符串，字符串正是创建的实际线路。
* ``PUT /api/v1/projects/{id}``（``UnifiedProjectUpdateRequest``）——for 循环逐条
  ``model_validate``，逐条的 loc 丢掉数组下标，第 2 条出错报成
  ``extraction_rules.type``：一个 payload 里根本不存在的路径。非可迭代入参
  更直接 TypeError，pydantic 只接管 ValueError，于是 422 变 500。
* ``PUT /api/v1/projects/{id}/rule-config``（``ProjectRuleUpdateRequest``）——没有
  自己造轮子，字段类型就是 ``list[ExtractionRule]``，loc 一直是对的。

第三条是活的对照组：它证明「交回给字段类型」本来就能产出正确的 loc，另外两条的
额外代码是纯粹的减分项。这里把三条钉在同一个 loc 上，防止再次分叉。

断言一律走结构化的 loc，不拿人类可读文案当契约。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from antcode_core.common.security.auth import get_current_user, get_current_user_id
from antcode_core.domain.schemas.project import ProjectRuleCreateRequest, ProjectRuleUpdateRequest
from antcode_core.domain.schemas.project_unified import UnifiedProjectUpdateRequest
from antcode_web_api.exceptions import (
    BusinessException,
    business_exception_handler,
    general_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from antcode_web_api.routes.v1.project import project_router
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

HTTP_UNPROCESSABLE_CONTENT = 422

# 第 0 条合法、第 1 条的 type 不在枚举内——下标必须出现在 loc 里才能定位到"第 1 条"
RULES_BAD_AT_INDEX_1: list[dict[str, Any]] = [
    {"desc": "标题", "type": "css", "expr": "h1"},
    {"desc": "正文", "type": "NOT_A_SELECTOR_KIND", "expr": "p"},
]
BAD_LOCUS = ("extraction_rules", 1, "type")

CREATE_BASE = {
    "name": "locus-probe",
    "type": "rule",
    "runtime_scope": "shared",
    "target_url": "https://example.com",
}


def _locs(exc_info: pytest.ExceptionInfo[ValidationError]) -> list[tuple[Any, ...]]:
    return [error["loc"] for error in exc_info.value.errors()]


def test_create_keeps_rule_index_when_rules_arrive_as_json_string() -> None:
    """multipart 只能承载字符串，字符串分支才是创建的实际线路——它不许退化。"""
    import ujson

    with pytest.raises(ValidationError) as exc_info:
        ProjectRuleCreateRequest(**CREATE_BASE, extraction_rules=ujson.dumps(RULES_BAD_AT_INDEX_1))

    assert _locs(exc_info) == [BAD_LOCUS]


def test_unified_update_keeps_rule_index() -> None:
    """曾经报成 ('extraction_rules', 'type')——下标丢了，指向不存在的路径。"""
    with pytest.raises(ValidationError) as exc_info:
        UnifiedProjectUpdateRequest(extraction_rules=RULES_BAD_AT_INDEX_1)

    assert _locs(exc_info) == [BAD_LOCUS]


def test_three_entry_points_agree_on_the_same_locus() -> None:
    """同一份坏数据，三条路由必须给出同一个 loc；分叉过一次就不许再分叉。"""
    produced = []
    for build in (
        lambda: ProjectRuleCreateRequest(**CREATE_BASE, extraction_rules=RULES_BAD_AT_INDEX_1),
        lambda: UnifiedProjectUpdateRequest(extraction_rules=RULES_BAD_AT_INDEX_1),
        lambda: ProjectRuleUpdateRequest(extraction_rules=RULES_BAD_AT_INDEX_1),
    ):
        with pytest.raises(ValidationError) as exc_info:
            build()
        produced.append(_locs(exc_info))

    assert produced == [[BAD_LOCUS], [BAD_LOCUS], [BAD_LOCUS]]


def test_unified_update_rejects_non_iterable_rules_instead_of_crashing() -> None:
    """``for rule in v`` 对 int 抛 TypeError，pydantic 不接管——这是 500 的来源。"""
    with pytest.raises(ValidationError) as exc_info:
        UnifiedProjectUpdateRequest(extraction_rules=123)

    assert _locs(exc_info) == [("extraction_rules",)]


# --- HTTP 层：非可迭代入参必须是 422，不能是 500 -----------------------------


def _build_app() -> FastAPI:
    """挂真实路由 + 真实异常处理器，只把鉴权换成测试桩。

    鉴权替身不是"mock 成功路径"：请求在业务逻辑之前就被校验拦下，
    真实的 update_project_unified 根本不会被调用。
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


def test_unified_route_reports_non_iterable_rules_as_422(client: TestClient) -> None:
    response = client.put("/api/v1/projects/p-1", json={"extraction_rules": 123})

    assert response.status_code == HTTP_UNPROCESSABLE_CONTENT
    payload = response.json()
    assert payload["data"] is not None, f"422 必须带逐字段原因，实得 {payload}"
    assert [error["field"] for error in payload["data"]["errors"]] == ["body.extraction_rules"]


def test_unified_route_reports_bad_rule_index_as_422(client: TestClient) -> None:
    response = client.put("/api/v1/projects/p-1", json={"extraction_rules": RULES_BAD_AT_INDEX_1})

    assert response.status_code == HTTP_UNPROCESSABLE_CONTENT
    fields = [error["field"] for error in response.json()["data"]["errors"]]
    assert fields == ["body.extraction_rules.1.type"]


# --- 控制组：没把正常路径挡死，落库形状也没变 -------------------------------

VALID_RULES: list[dict[str, Any]] = [{"desc": "标题", "type": "css", "expr": "h1"}]


def test_valid_rules_still_accepted_on_all_three_entry_points() -> None:
    """控制组（非证伪项）：合法输入三条路由照常通过，且都收敛成 ExtractionRule。"""
    import ujson

    created = ProjectRuleCreateRequest(**CREATE_BASE, extraction_rules=ujson.dumps(VALID_RULES))
    unified = UnifiedProjectUpdateRequest(extraction_rules=VALID_RULES)
    rule_config = ProjectRuleUpdateRequest(extraction_rules=VALID_RULES)

    for request in (created, unified, rule_config):
        assert request.extraction_rules is not None
        assert [rule.model_dump() for rule in request.extraction_rules] == [
            {"desc": "标题", "type": "css", "expr": "h1", "page_type": None}
        ]


def test_unified_update_keeps_stored_rule_shape_aligned_with_create() -> None:
    """控制组（非证伪项）：落库形状必须仍带 page_type。

    ``model_dump(exclude_unset=True)`` 会递归传进嵌套模型，客户端没显式写
    page_type 时整个键会消失，和创建链路（``project_service`` 的 ``rule.dict()``）
    存进库的形状就对不上了。
    """
    fields = UnifiedProjectUpdateRequest(extraction_rules=VALID_RULES).get_rule_fields()

    assert fields["extraction_rules"] == [{"desc": "标题", "type": "css", "expr": "h1", "page_type": None}]


def test_unified_update_omits_rules_when_client_did_not_send_them() -> None:
    """控制组（非证伪项）：部分更新语义不变——没传就不能出现在待写字段里。"""
    assert "extraction_rules" not in UnifiedProjectUpdateRequest(name="仅改名字").get_rule_fields()
