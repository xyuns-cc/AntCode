"""把创建项目的 multipart 表单组装成按类型收窄的 CreateRequest。

项目创建天然是两段式校验：multipart 只能承载字符串，所以第一段
``ProjectCreateFormRequest`` 把 extraction_rules / runtime_config 这类结构化字段
一律声明成 ``str``，逐字段的真实类型校验只能落到第二段——本模块按 type 选出
``ProjectRuleCreateRequest`` / ``ProjectFileCreateRequest`` / ``ProjectCodeCreateRequest``
再构造。第二段跑在 handler 函数体内，FastAPI 只把它自己解析入参时的
``ValidationError`` 转成 422，所以这里必须自己把错误翻译成
``RequestValidationError``，否则用户的输入错误会被报成 500。
"""

from __future__ import annotations

from typing import Any

from antcode_core.domain.models.enums import ProjectType
from antcode_core.domain.schemas.project import (
    ProjectCodeCreateRequest,
    ProjectCreateFormRequest,
    ProjectCreateRequest,
    ProjectFileCreateRequest,
    ProjectRuleCreateRequest,
)
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

CREATE_SCHEMA_BY_TYPE: dict[ProjectType, type[ProjectCreateRequest]] = {
    ProjectType.RULE: ProjectRuleCreateRequest,
    ProjectType.FILE: ProjectFileCreateRequest,
    ProjectType.CODE: ProjectCodeCreateRequest,
}


def build_project_create_request(form_data: ProjectCreateFormRequest) -> ProjectCreateRequest:
    request_data = {**_base_project_create_data(form_data), **_project_type_create_data(form_data)}
    schema = CREATE_SCHEMA_BY_TYPE.get(form_data.type, ProjectCreateRequest)
    try:
        return schema(**request_data)
    except ValidationError as exc:
        # 不翻译就落到 general_exception_handler 变成 500「服务器内部错误」，逐字段原因
        # 只进服务端日志、到不了调用方——把用户的输入错误谎报成服务端故障。
        raise RequestValidationError(_body_scoped_errors(exc)) from exc


def _body_scoped_errors(exc: ValidationError) -> list[Any]:
    """补上 body 前缀，让两段校验对外产出同一种 422 定位（FastAPI 解析 Form 时即用此 loc）。"""
    return [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]


def _extract_repo_source_fields(form_data: ProjectCreateFormRequest) -> dict[str, Any]:
    """O6: 从 FormRequest 抽 Git repository 源码字段，供 file/code project
    的 CreateRequest 使用。前端 ``appendRepositorySourceFields`` 用同一契约。
    """
    fields = {
        "repository_id": form_data.repository_id,
        "ref": form_data.ref or "main",
        "subdir": form_data.subdir,
        "include_paths": form_data.include_paths,
    }
    # 剔除 None 让 Pydantic default 生效
    return {k: v for k, v in fields.items() if v is not None}


def _base_project_create_data(form_data: ProjectCreateFormRequest) -> dict[str, Any]:
    return {
        "name": form_data.name,
        "description": form_data.description,
        "type": form_data.type,
        "tags": form_data.tags,
        "dependencies": form_data.dependencies,
        "runtime_scope": form_data.runtime_scope,
        "python_version": form_data.python_version,
        "shared_runtime_key": form_data.shared_runtime_key,
        "env_location": form_data.env_location,
        "worker_id": form_data.worker_id,
        "use_existing_env": form_data.use_existing_env,
        "existing_env_name": form_data.existing_env_name,
        "env_name": form_data.env_name,
        "env_description": form_data.env_description,
        "region": form_data.region,
    }


def _project_type_create_data(form_data: ProjectCreateFormRequest) -> dict[str, Any]:
    if form_data.type == ProjectType.FILE:
        return {
            "language": form_data.language,
            "entry_point": form_data.entry_point,
            "runtime_config": form_data.runtime_config,
            "environment_vars": form_data.environment_vars,
            **_extract_repo_source_fields(form_data),
        }
    if form_data.type == ProjectType.RULE:
        return _rule_project_create_data(form_data)
    if form_data.type == ProjectType.CODE:
        return {
            "language": form_data.language,
            "entry_point": form_data.code_entry_point,
            "documentation": form_data.documentation,
            **_extract_repo_source_fields(form_data),
        }
    return {}


def _rule_project_create_data(form_data: ProjectCreateFormRequest) -> dict[str, Any]:
    if not form_data.target_url:
        raise HTTPException(status_code=400, detail="规则项目必须提供target_url")
    if not form_data.extraction_rules:
        raise HTTPException(status_code=400, detail="规则项目必须提供extraction_rules")
    return {
        "engine": form_data.engine,
        "region": form_data.region,
        "require_render": form_data.require_render,
        "target_url": form_data.target_url,
        "url_pattern": form_data.url_pattern,
        "request_method": form_data.request_method,
        "callback_type": form_data.callback_type,
        "extraction_rules": form_data.extraction_rules,
        "pagination_config": form_data.pagination_config,
        "max_pages": form_data.max_pages,
        "start_page": form_data.start_page,
        "request_delay": form_data.request_delay,
        "retry_count": form_data.retry_count,
        "timeout": form_data.timeout,
        "priority": form_data.priority,
        "dont_filter": form_data.dont_filter,
        "data_schema": form_data.data_schema,
        "headers": form_data.headers,
        "cookies": form_data.cookies,
        "proxy_config": form_data.proxy_config,
        "anti_spider": form_data.anti_spider,
        "task_config": form_data.task_config,
        "resume_enabled": getattr(form_data, "resume_enabled", None),
        "dedup_config": getattr(form_data, "dedup_config", None),
    }
