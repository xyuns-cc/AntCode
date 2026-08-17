"""POST /api/v1/projects 的 multipart 线格式绑定（后端侧）。

契约由 contracts/http/project_create_form.json 单点定义：前端侧
`web/antcode-frontend/src/services/projectPayloads.contract.test.ts` 断言
`createProjectFormData` 逐条产出其中的 `form_entries`；本文件把同一批
`form_entries` 真发成 multipart 请求，断言后端解析出 `parsed_form` /
`parsed_create_request`。两段接起来，任何一侧单方面改编码方式都必定有一处红。

被这条绑定钉死的缺陷：前端把 include_paths 整体 JSON.stringify 成**一个**表单值，
后端却把它声明为 list[str] 交给 Starlette 按重复键收集，于是 `[]` 被解析成
`['[]']`、`['libs']` 被解析成 `['["libs"]']`，source bundle 随后对着名为 `[]` 的
目录 resolve_existing_dir 必然 FileNotFoundError——UI 建的每个 Git 项目都跑不了任务。
旧的单元测试用 `ProjectCreateFormRequest(include_paths='["libs/common"]')` 直接构造
模型，走的是 Form 路径下根本不可达的 str 分支，因此一直是绿的。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import pytest
from antcode_core.domain.schemas.project import ProjectCreateFormRequest
from antcode_web_api.routes.v1 import project as project_route
from fastapi import FastAPI, Form
from fastapi.testclient import TestClient

CONTRACT_PATH = Path("contracts/http/project_create_form.json")
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
CASES = CONTRACT["cases"]
CASE_IDS = [case["name"] for case in CASES]
HTTP_OK = 200


def _build_app() -> FastAPI:
    """挂真实依赖，让 FastAPI 的 Form 解析与路由层组装都进入断言范围。"""
    app = FastAPI()

    @app.post("/projects")
    async def create(  # pyright: ignore[reportUnusedFunction]
        form_data: Annotated[ProjectCreateFormRequest, Form()],
    ) -> dict[str, Any]:
        request = project_route._build_project_create_request(form_data)
        return {
            "parsed_form": {
                "include_paths": form_data.include_paths,
                "tags": form_data.tags,
                "dependencies": form_data.dependencies,
            },
            "parsed_create_request": {
                "include_paths": request.include_paths,
                "tags": request.tags,
                "dependencies": request.dependencies,
                "entry_point": request.entry_point,
            },
        }

    return app


def _post(entries: list[list[str]]) -> dict[str, Any]:
    """按线格式重放表单条目：重复同名键即列表，与浏览器发出的 FormData 一致。"""
    with TestClient(_build_app()) as client:
        response = client.post(
            "/projects",
            files=[(key, (None, value)) for key, value in entries],
        )
    assert response.status_code == HTTP_OK, response.text
    return response.json()


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_wire_entries_decode_to_the_contracted_values(case: dict[str, Any]) -> None:
    body = _post(case["form_entries"])

    assert body["parsed_form"] == case["parsed_form"]
    assert body["parsed_create_request"] == case["parsed_create_request"]


def test_contract_covers_empty_and_non_empty_include_paths() -> None:
    """空数组是 UI 默认路径，非空数组是共享目录路径，两者都必须被钉住。"""
    include_paths = [case["parsed_create_request"]["include_paths"] for case in CASES]

    assert [] in include_paths
    assert any(paths for paths in include_paths)


def test_repeated_keys_are_the_only_list_encoding() -> None:
    """整段 JSON 数组文本不再被当成数组解一层——那是静默兜底，也是脏数据的来源。

    契约改成「每个路径一个表单条目」后，客户端若仍旧发一整段 `["libs"]`，
    后端就把它当成一个字面量路径原样收下，随后在打包阶段以明确的
    「include_paths不存在」失败，而不是伪装成解析成功。
    """
    body = _post(
        [
            ["name", "wire-regression"],
            ["type", "code"],
            ["runtime_scope", "private"],
            ["python_version", "3.12"],
            ["worker_id", "worker-001"],
            ["repository_id", "repo-001"],
            ["subdir", "spiders/news"],
            ["include_paths", '["libs/common"]'],
            ["code_entry_point", "main.py"],
        ]
    )

    assert body["parsed_form"]["include_paths"] == ['["libs/common"]']


def test_include_paths_is_the_only_list_typed_form_field() -> None:
    """其余同源字段（tags / dependencies / runtime_config …）必须保持 str 声明。

    它们声明为 str 时才由各自的 `ProjectCreateRequest` 校验器解 JSON / 逗号分隔；
    一旦谁被改成 list[...]，就会重蹈 include_paths 的覆辙：Starlette 按重复键收集，
    整段 JSON 文本变成列表里的唯一字面量元素。
    """
    list_fields = {
        name for name, field in ProjectCreateFormRequest.model_fields.items() if "list" in str(field.annotation)
    }

    assert list_fields == {"include_paths"}


def test_form_schema_has_no_include_paths_string_fallback() -> None:
    """Form 路径下 include_paths 永远是列表，不允许再声明 str 兜底解析分支。"""
    assert "parse_include_paths" not in ProjectCreateFormRequest.__dict__
