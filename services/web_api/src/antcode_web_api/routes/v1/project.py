"""项目管理接口"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict

from antcode_core.application.services.audit import audit_service
from antcode_core.application.services.projects.project_service import project_service
from antcode_core.application.services.projects.project_source_service import (
    project_source_service,
)
from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.application.services.projects.unified_project_service import unified_project_service
from antcode_core.application.services.users.user_service import user_service
from antcode_core.common.security.auth import get_current_user, get_current_user_id
from antcode_core.common.utils.api_optimizer import (
    fast_response,
    monitor_performance,
    optimize_large_response,
)
from antcode_core.domain.models import Task, TaskRun, User
from antcode_core.domain.models.audit_log import AuditAction
from antcode_core.domain.models.enums import ProjectStatus, ProjectType
from antcode_core.domain.models.project import Project, ProjectCode, ProjectFile, ProjectRule
from antcode_core.domain.schemas.common import BaseResponse, PaginationResponse
from antcode_core.domain.schemas.project import (
    CodeInfo,
    FileInfo,
    ProjectCodeCreateRequest,
    ProjectCodeUpdateRequest,
    ProjectCreateFormRequest,
    ProjectCreateRequest,
    ProjectFileCreateRequest,
    ProjectFileUpdateRequest,
    ProjectListQueryRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectRuleCreateRequest,
    TaskJsonRequest,
)
from antcode_core.domain.schemas.project_source import ProjectSourcePayload, ProjectSourceResponse
from antcode_core.domain.schemas.project_unified import UnifiedProjectUpdateRequest
from fastapi import (
    APIRouter,
    Body,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict

from antcode_web_api.exceptions import ProjectNotFoundException
from antcode_web_api.response import (
    ExecutionResponseBuilder,
    Messages,
    ProjectResponseBuilder,
)
from antcode_web_api.response import (
    page as page_response,
)
from antcode_web_api.response import (
    success as success_response,
)

project_router = APIRouter()

# 批量删除单次上限，避免单个请求占满后台执行资源。
BATCH_DELETE_MAX_PROJECTS = 100


class SourceResponseFields(TypedDict):
    repository_id: str
    repository_name: str
    repository_url: str
    ref: str
    subdir: str
    include_paths: list[str]
    resolved_revision: str | None


def create_project_response(project: Project) -> ProjectResponse:
    """构建项目响应"""
    return ProjectResponseBuilder.build_detail(project)


class ProjectDuplicateRequest(BaseModel):
    name: str | None = None


class ProjectExportRequest(BaseModel):
    format: str = "json"
    include_tasks: bool | None = None
    include_logs: bool | None = None
    date_range: dict[str, str] | None = None


class ProjectValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    type: str | None = None
    runtime_scope: str | None = None
    python_version: str | None = None
    target_url: str | None = None
    extraction_rules: str | dict | None = None
    repository_id: str | None = None
    subdir: str | None = None
    entry_point: str | None = None


def _yaml_dump(data: object, indent: int = 0) -> str:
    """简单 YAML 序列化（避免额外依赖）"""
    prefix = "  " * indent
    if isinstance(data, dict):
        lines = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(_yaml_dump(value, indent + 1))
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(value)}")
        return "\n".join(lines)
    if isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(_yaml_dump(item, indent + 1))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{prefix}{_yaml_scalar(data)}"


def _yaml_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if any(c in text for c in [":", "-", "#", "\n", '"', "'"]):
        return json.dumps(text, ensure_ascii=False)
    return text


async def _generate_unique_project_name(base_name: str) -> str:
    name = base_name
    idx = 1
    while await Project.filter(name=name).exists():
        name = f"{base_name}-{idx}"
        idx += 1
    return name


def _task_export_payload(task: Task, project_public_id: str) -> dict[str, object]:
    """导出任务配置（适配 TaskCreateRequest）"""
    return {
        "name": task.name,
        "description": task.description or "",
        "project_id": project_public_id,
        "schedule_type": task.schedule_type,
        "cron_expression": task.cron_expression,
        "interval_seconds": task.interval_seconds,
        "scheduled_time": task.scheduled_time.isoformat() if task.scheduled_time else None,
        "max_instances": task.max_instances,
        "timeout_seconds": task.timeout_seconds,
        "retry_count": task.retry_count,
        "retry_delay": task.retry_delay,
        "execution_params": task.execution_params or {},
        "environment_vars": task.environment_vars or {},
        "is_active": task.is_active,
        "execution_strategy": task.execution_strategy,
        "specified_worker_id": None,
    }


CREATE_PROJECT_FORM_FIELDS = frozenset(ProjectCreateFormRequest.model_fields)


async def get_project_create_form(
    form_data: Annotated[ProjectCreateFormRequest, Form()],
) -> ProjectCreateFormRequest:
    """使用单一严格 schema 解析 multipart 表单。"""
    return form_data


def _extract_repo_source_fields(form_data) -> dict:
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


async def get_project_list_query(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=500),
    project_type: ProjectType | None = Query(None, alias="type"),
    status: ProjectStatus | None = Query(None),
    tag: str | None = Query(None),
    created_by: str | None = Query(None),
    search: str | None = Query(None),
    worker_id: str | None = Query(None, description="Worker ID 筛选"),
):
    return ProjectListQueryRequest(
        page=page,
        size=size,
        type=project_type,
        status=status,
        tag=tag,
        created_by=created_by,
        search=search,
        worker_id=worker_id,
    )


# P2-25: 批量删除单次上限, 防止公开 API 被打爆
@project_router.post(
    "",
    response_model=BaseResponse[ProjectResponse],
    status_code=status.HTTP_201_CREATED,
    summary="创建项目",
    description="创建新项目，支持文件、规则、代码三种类型",
    response_description="返回创建的项目信息",
)
async def create_project(
    http_request: Request,
    form_data=Depends(get_project_create_form),
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """创建项目。

    O6: 之前该端点因三重漂移（service 签名多传 File kwarg / FormRequest
    缺 10 字段 / Create schema 缺 repository_id）当前 100% 失败。
    修复方向：
    - 删除已废弃的 file/files/code_file File 参数——新架构下项目源码统一
      走 Git repository，不再接受 upload；前端也已不再传这些字段。
    - 请求字段严格匹配当前 CreateRequest，源码只接受仓库绑定字段。
    - 废弃字段不再声明为 Form 参数，避免客户端误以为这些配置仍然生效。
    """

    request = _build_project_create_request(form_data)
    project = await project_service.create_project(
        request=request,
        user_id=current_user_id,
    )
    response_data = create_project_response(project)
    await _attach_project_detail_info(response_data, project)
    await _audit_project_creation(http_request, current_user, project)
    return success_response(response_data, message=Messages.CREATED_SUCCESS, code=201)


def _build_project_create_request(form_data: ProjectCreateFormRequest) -> ProjectCreateRequest:
    request_data = {**_base_project_create_data(form_data), **_project_type_create_data(form_data)}
    schema_by_type = {
        ProjectType.RULE: ProjectRuleCreateRequest,
        ProjectType.FILE: ProjectFileCreateRequest,
        ProjectType.CODE: ProjectCodeCreateRequest,
    }
    schema = schema_by_type.get(form_data.type, ProjectCreateRequest)
    return schema(**request_data)


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
    }


def _project_type_create_data(form_data: ProjectCreateFormRequest) -> dict[str, Any]:
    if form_data.type == ProjectType.FILE:
        return {
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


async def _audit_project_creation(http_request: Request, current_user: Any, project: Project) -> None:
    user = await user_service.get_user_by_id(current_user.user_id)
    await audit_service.log_project_action(
        action=AuditAction.PROJECT_CREATE,
        username=user.username if user else "unknown",
        project_id=project.id,
        project_name=project.name,
        user_id=current_user.user_id,
        ip_address=http_request.client.host if http_request.client else None,
        description=f"创建项目: {project.name} (类型: {project.type})",
    )


@project_router.get(
    "",
    response_model=PaginationResponse[ProjectListResponse],
    summary="获取项目列表",
    description="获取当前用户的项目列表，支持分页和筛选",
)
@fast_response(cache_ttl=120, namespace="project:list")
@monitor_performance(slow_threshold=0.5)
@optimize_large_response(chunk_size=50)
async def get_projects_list(
    query_params=Depends(get_project_list_query),
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """获取项目列表"""
    is_admin = await user_service.is_admin(current_user.user_id)
    user_filter = None if is_admin else current_user_id
    if is_admin and query_params.created_by is not None:
        try:
            user_filter = int(query_params.created_by)
        except (ValueError, TypeError):
            user = await User.filter(public_id=query_params.created_by).first()
            user_filter = user.id if user else None

    projects, total = await project_service.get_projects_list(
        page=query_params.page,
        size=query_params.size,
        project_type=query_params.type,
        status=query_params.status.value if query_params.status else None,
        tag=query_params.tag,
        user_id=user_filter,
        search=query_params.search,
        worker_id=query_params.worker_id,
    )

    return page_response(
        items=ProjectResponseBuilder.build_list(projects),
        total=total,
        page=query_params.page,
        size=query_params.size,
        message=Messages.QUERY_SUCCESS,
    )


@project_router.get(
    "/stats",
    response_model=BaseResponse[dict],
    summary="获取项目统计信息",
)
async def get_project_stats(current_user_id=Depends(get_current_user_id), current_user=Depends(get_current_user)):
    """获取项目统计信息"""
    is_admin = await user_service.is_admin(current_user.user_id)
    query = Project.all() if is_admin else Project.filter(user_id=current_user_id)

    total = await query.count()
    active = await query.filter(status="active").count()
    inactive = await query.filter(status="inactive").count()
    archived = await query.filter(status="archived").count()

    file_count = await query.filter(type=ProjectType.FILE).count()
    rule_count = await query.filter(type=ProjectType.RULE).count()
    code_count = await query.filter(type=ProjectType.CODE).count()

    recent = await query.order_by("-created_at").limit(10)

    data = {
        "total_projects": total,
        "active_projects": active,
        "inactive_projects": inactive,
        "error_projects": archived,
        "projects_by_type": {
            "file": file_count,
            "rule": rule_count,
            "code": code_count,
        },
        "recent_projects": ProjectResponseBuilder.build_list(recent),
    }

    return success_response(data, message=Messages.QUERY_SUCCESS)


@project_router.get(
    "/tags",
    response_model=BaseResponse[dict],
    summary="获取项目标签列表",
)
async def list_project_tags(current_user_id=Depends(get_current_user_id), current_user=Depends(get_current_user)):
    """获取项目标签列表"""
    is_admin = await user_service.is_admin(current_user.user_id)
    query = Project.all() if is_admin else Project.filter(user_id=current_user_id)
    projects = await query.only("tags")

    tags: set[str] = set()
    for proj in projects:
        if isinstance(proj.tags, list):
            tags.update({str(tag) for tag in proj.tags})
        elif proj.tags:
            tags.add(str(proj.tags))

    return success_response({"tags": sorted(tags)}, message=Messages.QUERY_SUCCESS)


@project_router.get(
    "/search",
    response_model=BaseResponse[dict],
    summary="搜索项目",
)
async def search_projects(
    query_params=Depends(get_project_list_query),
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """搜索项目"""
    is_admin = await user_service.is_admin(current_user.user_id)
    user_filter = None if is_admin else current_user_id

    projects, _ = await project_service.get_projects_list(
        page=query_params.page,
        size=query_params.size,
        project_type=query_params.type,
        status=query_params.status.value if query_params.status else None,
        tag=query_params.tag,
        user_id=user_filter,
        search=query_params.search,
        worker_id=query_params.worker_id,
    )

    return success_response(
        {"projects": ProjectResponseBuilder.build_list(projects)},
        message=Messages.QUERY_SUCCESS,
    )


@project_router.post(
    "/validate",
    response_model=BaseResponse[dict],
    summary="验证项目配置",
)
async def validate_project_config(
    payload: ProjectValidateRequest,
    current_user=Depends(get_current_user),
):
    """验证项目配置"""
    _ = current_user
    errors = _required_project_errors(payload)
    _validate_project_type(payload, errors)

    return success_response(
        {"valid": len(errors) == 0, "errors": errors if errors else None},
        message=Messages.QUERY_SUCCESS,
    )


def _required_project_errors(payload: ProjectValidateRequest) -> list[str]:
    required = (
        (payload.name, "项目名称不能为空"),
        (payload.type, "项目类型不能为空"),
        (payload.runtime_scope, "运行时作用域不能为空"),
        (payload.python_version, "Python 版本不能为空"),
    )
    return [message for value, message in required if not value]


def _validate_project_type(payload: ProjectValidateRequest, errors: list[str]) -> None:
    project_type = payload.type.lower() if payload.type else None
    if project_type == ProjectType.RULE.value:
        if not payload.target_url:
            errors.append("规则项目必须提供 target_url")
        if not payload.extraction_rules:
            errors.append("规则项目必须提供 extraction_rules")
        return
    if project_type == ProjectType.FILE.value:
        _validate_repository_project(payload, "文件", errors)
    elif project_type == ProjectType.CODE.value:
        _validate_repository_project(payload, "代码", errors)


def _validate_repository_project(
    payload: ProjectValidateRequest,
    project_label: str,
    errors: list[str],
) -> None:
    if not payload.repository_id:
        errors.append(f"Git {project_label}项目必须提供 repository_id")
    if not payload.subdir:
        errors.append(f"Git {project_label}项目必须提供 subdir")
    if not payload.entry_point:
        errors.append(f"Git {project_label}项目必须提供 entry_point")


@project_router.post(
    "/import",
    response_model=BaseResponse[list[ProjectResponse]],
    summary="导入文件项目",
)
async def import_projects(
    current_user_id=Depends(get_current_user_id),
):
    """[已下线] 上传文件建文件项目。

    O6/迁移 44 后新架构下文件项目源码统一走 Git 仓库；请改用：
    ``POST /api/v1/repositories/import-from-repository``（同时支持 code/file
    项目）。此端点保留仅为让前端收到明确错误信息，而非静默 500。
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "上传式文件项目导入已下线。请先在 /api/v1/repositories 创建 Git 仓库"
            "，然后调用 POST /api/v1/repositories/import-from-repository 导入项目。"
        ),
    )


@project_router.post(
    "/{project_id}/export",
    summary="导出项目配置",
    response_model=None,
)
async def export_project_config(
    project_id: str,
    request: ProjectExportRequest,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """导出项目配置"""
    project = await project_service.get_project_by_id(project_id, current_user_id)
    if not project:
        raise ProjectNotFoundException(project_id)

    response_data = create_project_response(project)
    await _attach_project_detail_info(response_data, project)
    project_payload = response_data.model_dump(mode="json")

    payload: dict[str, object] = {
        "version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "project": project_payload,
    }

    include_tasks = bool(request.include_tasks)
    include_logs = bool(request.include_logs)
    task_items = await _load_export_tasks(
        project,
        include_tasks=include_tasks,
        current_user_id=current_user_id,
        is_admin=await user_service.is_admin(current_user.user_id),
    )
    if include_tasks:
        payload["tasks"] = task_items

    if include_logs and task_items:
        payload["executions"] = await _load_export_executions(project, request.date_range)

    fmt = (request.format or "json").lower()
    content, media_type, filename = _render_project_export(
        fmt,
        payload,
        project,
        include_tasks=include_tasks,
        task_items=task_items,
    )

    buffer = io.BytesIO(content.encode("utf-8"))
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(buffer, media_type=media_type, headers=headers)


async def _load_export_tasks(
    project: Project,
    *,
    include_tasks: bool,
    current_user_id: int,
    is_admin: bool,
) -> list[dict[str, object]]:
    if not include_tasks:
        return []
    task_query = Task.filter(project_id=project.id)
    if not is_admin:
        task_query = task_query.filter(user_id=current_user_id)
    tasks = await task_query.all()
    return [_task_export_payload(task, project.public_id) for task in tasks]


async def _load_export_executions(project: Project, date_range: dict[str, str] | None) -> list[Any]:
    task_ids = await Task.filter(project_id=project.id).values_list("id", flat=True)
    run_query = TaskRun.filter(task_id__in=list(task_ids))
    run_query = _filter_export_date_range(run_query, date_range)
    runs = await run_query.order_by("-created_at").limit(200)
    return ExecutionResponseBuilder.build_list(runs)


def _filter_export_date_range(run_query: Any, date_range: dict[str, str] | None) -> Any:
    if not date_range:
        return run_query
    start = _parse_export_date(date_range.get("start"), "start")
    end = _parse_export_date(date_range.get("end"), "end")
    if start:
        run_query = run_query.filter(created_at__gte=start)
    if end:
        run_query = run_query.filter(created_at__lte=end)
    return run_query


def _parse_export_date(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"date_range.{field} 格式错误")


def _render_project_export(
    fmt: str,
    payload: dict[str, object],
    project: Project,
    *,
    include_tasks: bool,
    task_items: list[dict[str, object]],
) -> tuple[str, str, str]:
    filename_prefix = f"project_{project.public_id}"
    if fmt == "yaml":
        return _yaml_dump(payload), "text/yaml", f"{filename_prefix}.yaml"
    if fmt == "csv":
        content = _render_project_csv(payload["project"], task_items, include_tasks)
        return content, "text/csv", f"{filename_prefix}.csv"
    return json.dumps(payload, ensure_ascii=False, indent=2), "application/json", f"{filename_prefix}.json"


def _render_project_csv(
    project_payload: object,
    task_items: list[dict[str, object]],
    include_tasks: bool,
) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    if include_tasks and task_items:
        _write_task_export_rows(writer, task_items)
    else:
        _write_project_export_rows(writer, project_payload)
    return output.getvalue()


def _write_task_export_rows(writer: Any, task_items: list[dict[str, object]]) -> None:
    writer.writerow(["task_id", "name", "schedule_type", "is_active", "status", "project_id"])
    for item in task_items:
        writer.writerow(
            ["", item.get("name"), item.get("schedule_type"), item.get("is_active"), "", item.get("project_id")]
        )


def _write_project_export_rows(writer: Any, project_payload: object) -> None:
    if not isinstance(project_payload, dict):
        raise TypeError("project export payload must be a mapping")
    writer.writerow(["project_id", "name", "type", "status", "description", "tags"])
    writer.writerow(
        [
            project_payload.get("id"),
            project_payload.get("name"),
            project_payload.get("type"),
            project_payload.get("status"),
            project_payload.get("description"),
            ",".join(project_payload.get("tags") or []),
        ]
    )


@project_router.get(
    "/{project_id}",
    response_model=BaseResponse[ProjectResponse],
    summary="获取项目详情",
)
@fast_response(
    cache_ttl=120,
    namespace="project:detail",
    key_prefix_fn=lambda args, kwargs: str(kwargs.get("project_id", args[0] if args else "")),
)
async def get_project_detail(project_id: str, current_user_id: int = Depends(get_current_user_id)):
    """获取项目详情"""
    project = await project_service.get_project_by_id(project_id, current_user_id)
    if not project:
        raise ProjectNotFoundException(project_id)

    response_data = create_project_response(project)
    await _attach_project_detail_info(response_data, project)
    return success_response(response_data, message=Messages.QUERY_SUCCESS)


@project_router.get(
    "/{project_id}/source",
    response_model=BaseResponse[ProjectSourceResponse],
    summary="获取项目 Git 来源",
)
async def get_project_source(
    project_id: str,
    current_user_id: int = Depends(get_current_user_id),
):
    project = await _get_repository_project(project_id, current_user_id)
    try:
        source = await project_source_service.get_response(project.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return success_response(source, message=Messages.QUERY_SUCCESS)


@project_router.put(
    "/{project_id}/source",
    response_model=BaseResponse[ProjectSourceResponse],
    summary="更新项目 Git 来源",
)
async def update_project_source(
    project_id: str,
    payload: ProjectSourcePayload,
    current_user_id: int = Depends(get_current_user_id),
):
    project = await _get_repository_project(project_id, current_user_id)
    try:
        source = await project_source_service.update_from_payload(
            project.id,
            payload,
            owner_user_id=current_user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return success_response(source, message=Messages.UPDATED_SUCCESS)


async def _get_repository_project(project_id: str, current_user_id: int):
    project = await project_service.get_project_by_id(project_id, current_user_id)
    if project is None:
        raise ProjectNotFoundException(project_id)
    if project.type not in {ProjectType.FILE, ProjectType.CODE}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="规则项目没有 Git 项目来源")
    return project


async def _attach_project_detail_info(response_data: ProjectResponse, project):
    """为项目响应附加详细信息。"""
    if project.type == ProjectType.FILE:
        if detail := await relation_service.get_project_file_detail(project.id):
            source = await project_source_service.get_response(project.id)
            response_data.file_info = FileInfo(
                entry_point=detail.entry_point,
                runtime_config={},
                environment_vars={},
                **_source_response_fields(source),
            )
    elif project.type == ProjectType.RULE:
        if detail := await relation_service.get_project_rule_detail(project.id):
            response_data.rule_info = {
                "engine": detail.engine,
                "target_url": detail.target_url,
                "url_pattern": detail.url_pattern,
                "callback_type": detail.callback_type,
                "request_method": detail.request_method,
                "extraction_rules": detail.extraction_rules,
                "data_schema": detail.data_schema,
                "pagination_config": detail.pagination_config,
                "max_pages": detail.max_pages,
                "start_page": detail.start_page,
                "request_delay": detail.request_delay,
                "retry_count": detail.retry_count,
                "timeout": detail.timeout,
                "priority": getattr(detail, "priority", 0),
                "dont_filter": getattr(detail, "dont_filter", False),
                "headers": None,
                "cookies": None,
                "proxy_config": None,
                "task_config": None,
                # S10: 详情页展示这两个字段
                "resume_enabled": bool(getattr(detail, "resume_enabled", False) or False),
                "dedup_config": getattr(detail, "dedup_config", None),
            }
    elif project.type == ProjectType.CODE and (detail := await relation_service.get_project_code_detail(project.id)):
        source = await project_source_service.get_response(project.id)
        response_data.code_info = CodeInfo(
            language=detail.language,
            entry_point=detail.entry_point,
            runtime_config={},
            environment_vars={},
            documentation=detail.documentation,
            **_source_response_fields(source),
        )


def _source_response_fields(source: ProjectSourceResponse) -> SourceResponseFields:
    return {
        "repository_id": source.repository_id,
        "repository_name": source.repository_name,
        "repository_url": source.repository_url,
        "ref": source.ref,
        "subdir": source.subdir,
        "include_paths": source.include_paths,
        "resolved_revision": source.resolved_commit,
    }


@project_router.put("/{project_id}", response_model=BaseResponse[ProjectResponse], summary="更新项目")
async def update_project(
    project_id: str,
    request: UnifiedProjectUpdateRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    """统一更新项目"""
    project = await unified_project_service.update_project_unified(project_id, request, current_user_id)
    if not project:
        raise ProjectNotFoundException(project_id)

    response_data = create_project_response(project)
    await _attach_project_detail_info(response_data, project)
    return success_response(response_data, message=Messages.UPDATED_SUCCESS)


@project_router.post(
    "/{project_id}/duplicate",
    response_model=BaseResponse[ProjectResponse],
    summary="复制项目",
)
async def duplicate_project(
    project_id: str,
    payload: ProjectDuplicateRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    """复制项目

    T11: 全程包裹在数据库事务中，避免主 Project 创建成功后子表（File/Rule/Code）
    复制失败时留下孤儿项目记录。
    """
    from tortoise.transactions import in_transaction

    project = await project_service.get_project_by_id(project_id, current_user_id)
    if not project:
        raise ProjectNotFoundException(project_id)

    base_name = payload.name or f"{project.name}-copy"
    name = await _generate_unique_project_name(base_name)

    async with in_transaction():
        new_project = await _create_duplicate_project(project, name, current_user_id)
        await _duplicate_project_detail(project, new_project)

    response_data = create_project_response(new_project)
    await _attach_project_detail_info(response_data, new_project)
    return success_response(response_data, message=Messages.CREATED_SUCCESS)


async def _create_duplicate_project(project: Project, name: str, user_id: int) -> Project:
    return await Project.create(
        name=name,
        description=project.description,
        type=project.type,
        status=project.status,
        tags=project.tags or [],
        dependencies=project.dependencies,
        env_location=project.env_location,
        worker_id=project.worker_id,
        worker_env_name=project.worker_env_name,
        python_version=project.python_version,
        runtime_scope=project.runtime_scope,
        runtime_kind=project.runtime_kind,
        runtime_locator=project.runtime_locator,
        current_runtime_id=project.current_runtime_id,
        runtime_worker_id=project.runtime_worker_id,
        execution_strategy=project.execution_strategy,
        bound_worker_id=project.bound_worker_id,
        user_id=user_id,
        updated_by=user_id,
    )


async def _duplicate_project_detail(source: Project, target: Project) -> None:
    if source.type == ProjectType.FILE:
        detail = await relation_service.get_project_file_detail(source.id)
        if detail:
            await _duplicate_file_detail(detail, target.id)
    elif source.type == ProjectType.RULE:
        detail = await relation_service.get_project_rule_detail(source.id)
        if detail:
            await _duplicate_rule_detail(detail, target.id)
    elif source.type == ProjectType.CODE:
        detail = await relation_service.get_project_code_detail(source.id)
        if detail:
            await _duplicate_code_detail(detail, target.id)


async def _duplicate_file_detail(detail: Any, project_id: int) -> None:
    await ProjectFile.create(
        project_id=project_id,
        language=detail.language,
        entry_point=detail.entry_point,
        runtime_config=detail.runtime_config,
        environment_vars=detail.environment_vars,
    )


async def _duplicate_rule_detail(detail: Any, project_id: int) -> None:
    await ProjectRule.create(
        project_id=project_id,
        engine=detail.engine,
        target_url=detail.target_url,
        url_pattern=detail.url_pattern,
        callback_type=detail.callback_type,
        request_method=detail.request_method,
        extraction_rules=detail.extraction_rules,
        data_schema=detail.data_schema,
        pagination_config=detail.pagination_config,
        max_pages=detail.max_pages,
        start_page=detail.start_page,
        request_delay=detail.request_delay,
        retry_count=detail.retry_count,
        timeout=detail.timeout,
        priority=getattr(detail, "priority", 0),
        dont_filter=getattr(detail, "dont_filter", False),
        headers=detail.headers,
        cookies=detail.cookies,
        proxy_config=detail.proxy_config,
        anti_spider=detail.anti_spider,
        task_config=getattr(detail, "task_config", None),
    )


async def _duplicate_code_detail(detail: Any, project_id: int) -> None:
    await ProjectCode.create(
        project_id=project_id,
        language=detail.language,
        entry_point=detail.entry_point,
        runtime_config=detail.runtime_config,
        environment_vars=detail.environment_vars,
    )


@project_router.post(
    "/{project_id}/test-connection",
    response_model=BaseResponse[dict],
    summary="测试规则项目连接",
)
async def test_project_connection(
    project_id: str,
    current_user_id: int = Depends(get_current_user_id),
):
    """测试规则项目连接"""
    project = await project_service.get_project_by_id(project_id, current_user_id)
    if not project:
        raise ProjectNotFoundException(project_id)
    if project.type != ProjectType.RULE:
        raise HTTPException(status_code=400, detail="仅支持规则项目测试连接")

    detail = await relation_service.get_project_rule_detail(project.id)
    if not detail:
        raise HTTPException(status_code=400, detail="规则项目配置不完整")

    url = detail.target_url
    method = detail.request_method.value if hasattr(detail.request_method, "value") else str(detail.request_method)
    headers = detail.headers if isinstance(detail.headers, dict) else None
    cookies = detail.cookies if isinstance(detail.cookies, dict) else None

    try:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.request(method.upper(), url, headers=headers, cookies=cookies)
        ok = resp.status_code < 400
        return success_response(
            {"success": ok, "message": "连接成功" if ok else "连接失败", "data": {"status_code": resp.status_code}},
            message=Messages.QUERY_SUCCESS,
        )
    except Exception as exc:
        return success_response(
            {"success": False, "message": f"连接失败: {exc}"},
            message=Messages.QUERY_SUCCESS,
        )


@project_router.get(
    "/{project_id}/dependencies",
    response_model=BaseResponse[dict],
    summary="获取项目依赖",
)
async def get_project_dependencies(
    project_id: str,
    current_user_id: int = Depends(get_current_user_id),
):
    """获取项目依赖"""
    project = await project_service.get_project_by_id(project_id, current_user_id)
    if not project:
        raise ProjectNotFoundException(project_id)

    deps = project.dependencies if isinstance(project.dependencies, list) else []
    return success_response({"dependencies": deps}, message=Messages.QUERY_SUCCESS)


@project_router.put(
    "/{project_id}/dependencies",
    response_model=BaseResponse[dict],
    summary="更新项目依赖",
)
async def update_project_dependencies(
    project_id: str,
    payload: dict,
    current_user_id: int = Depends(get_current_user_id),
):
    """更新项目依赖"""
    project = await project_service.get_project_by_id(project_id, current_user_id)
    if not project:
        raise ProjectNotFoundException(project_id)

    deps = payload.get("dependencies") if isinstance(payload, dict) else None
    if deps is None:
        raise HTTPException(status_code=400, detail="必须提供 dependencies")
    if not isinstance(deps, list):
        raise HTTPException(status_code=400, detail="dependencies 必须为数组")

    project.dependencies = deps
    project.updated_by = current_user_id
    await project.save()
    return success_response({"dependencies": deps}, message=Messages.UPDATED_SUCCESS)


@project_router.delete(
    "/{project_id}",
    response_model=BaseResponse[None],
    summary="删除项目",
    description="删除指定的项目（不可逆操作）",
    response_description="返回删除操作结果",
)
async def delete_project(
    project_id,
    http_request: Request,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    # 获取项目信息用于审计
    project = await project_service.get_project_by_id(project_id, current_user_id)
    project_name = project.name if project else project_id

    # 删除项目
    deleted = await project_service.delete_project(project_id, current_user_id)
    if not deleted:
        raise ProjectNotFoundException(project_id)

    # 记录审计日志
    user = await user_service.get_user_by_id(current_user.user_id)
    await audit_service.log_project_action(
        action=AuditAction.PROJECT_DELETE,
        username=user.username if user else "unknown",
        project_id=project.id if project else 0,
        project_name=project_name,
        user_id=current_user.user_id,
        ip_address=http_request.client.host if http_request.client else None,
        description=f"删除项目: {project_name}",
    )

    return success_response(None, message=Messages.DELETED_SUCCESS)


@project_router.post(
    "/batch-delete",
    response_model=BaseResponse[dict],
    summary="批量删除项目",
    description="批量删除多个项目（不可逆操作）",
    response_description="返回批量删除操作结果",
)
@fast_response(background_execution=True)  # 后台执行
@monitor_performance(slow_threshold=2.0)  # 监控超过2秒的批量操作
async def batch_delete_projects(request=Body(...), current_user_id=Depends(get_current_user_id)):
    """批量删除项目"""
    project_ids = request.get("project_ids", [])
    if not project_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="项目ID列表不能为空")

    # P2-25: 强制单次上限, 防止调用方一次性拖垮后台 semaphore + DB
    if len(project_ids) > BATCH_DELETE_MAX_PROJECTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"批量删除单次上限 {BATCH_DELETE_MAX_PROJECTS} 个项目, 当前 {len(project_ids)} 个",
        )

    success_count = 0
    failed_count = 0
    failed_projects = []

    for project_id in project_ids:
        try:
            success = await project_service.delete_project(project_id, current_user_id)
            if success:
                success_count += 1
            else:
                failed_count += 1
                failed_projects.append(project_id)
        except Exception:
            failed_count += 1
            failed_projects.append(project_id)

    result_data = {
        "total": len(project_ids),
        "success_count": success_count,
        "failed_count": failed_count,
        "failed_projects": failed_projects,
    }

    if failed_count == 0:
        message = f"成功删除 {success_count} 个项目"
    elif success_count == 0:
        message = f"删除失败，{failed_count} 个项目删除失败"
    else:
        message = f"部分成功：{success_count} 个项目删除成功，{failed_count} 个项目删除失败"

    return success_response(result_data, message=message)


@project_router.get(
    "/{project_id}/task-json",
    response_model=BaseResponse[TaskJsonRequest],
    summary="生成任务JSON",
    description="根据规则项目配置生成爬虫任务JSON",
    response_description="返回任务JSON配置",
)
async def generate_task_json(project_id, current_user_id=Depends(get_current_user_id)):
    """生成任务JSON"""

    # 获取项目详情
    project = await project_service.get_project_by_id(project_id, current_user_id)
    if not project:
        raise ProjectNotFoundException(project_id)

    # 检查项目类型
    if project.type != ProjectType.RULE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="只有规则项目才能生成任务JSON",
        )

    # 获取规则详情 - 使用应用层关联
    rule_detail = await relation_service.get_project_rule_detail(project.id)
    if not rule_detail:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="规则项目配置不完整")

    # 构建任务JSON
    task_json = await project_service.generate_task_json(rule_detail)

    return success_response(task_json, message="任务JSON生成成功")


@project_router.put(
    "/{project_id}/rule-config",
    response_model=BaseResponse[ProjectResponse],
    summary="更新规则项目配置",
    description="更新规则项目的详细配置",
    response_description="返回更新后的项目信息",
)
async def update_rule_config(project_id, request, current_user_id=Depends(get_current_user_id)):
    """更新规则项目配置"""

    # 获取项目详情
    project = await project_service.get_project_by_id(project_id, current_user_id)
    if not project:
        raise ProjectNotFoundException(project_id)

    # 检查项目类型
    if project.type != ProjectType.RULE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="只有规则项目才能更新规则配置",
        )

    # 更新规则配置
    updated_project = await project_service.update_rule_config(project_id, request, current_user_id)

    # 构建响应数据
    response_data = create_project_response(updated_project)

    return success_response(response_data, message=Messages.UPDATED_SUCCESS)


@project_router.put("/{project_id}/code-config", response_model=BaseResponse[ProjectResponse])
async def update_code_config(
    project_id: str,
    request: ProjectCodeUpdateRequest,
    current_user_id=Depends(get_current_user_id),
):
    """更新代码项目配置"""
    try:
        # 更新代码配置
        updated_project = await project_service.update_code_config(project_id, request, current_user_id)

        if not updated_project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在或无权限访问")

        # 构建响应数据
        response_data = create_project_response(updated_project)
        await _attach_project_detail_info(response_data, updated_project)

        return success_response(response_data, message=Messages.UPDATED_SUCCESS)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新代码项目配置失败: {str(e)}",
        )


@project_router.put(
    "/{project_id}/file-config",
    response_model=BaseResponse[ProjectResponse],
    summary="更新文件项目配置",
    description="更新文件项目的详细配置，支持文件替换",
    response_description="返回更新后的项目信息",
)
async def update_file_config(
    project_id: str,
    entry_point=Form(None),
    runtime_config=Form(None),
    environment_vars=Form(None),
    current_user_id=Depends(get_current_user_id),
):
    """更新 Git FILE 项目的运行配置，不处理源码变更。"""
    try:
        # 获取项目详情
        project = await project_service.get_project_by_id(project_id, current_user_id)
        if not project:
            raise ProjectNotFoundException(project_id)

        # 检查项目类型
        if project.type != ProjectType.FILE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有文件项目才能更新文件配置",
            )

        # 构建更新请求 —— schema (extra="forbid") 只接受这 3 个字段
        request = ProjectFileUpdateRequest(
            entry_point=entry_point,
            runtime_config=runtime_config,
            environment_vars=environment_vars,
        )

        # 更新文件配置 —— service.update_file_config 签名是 (project_id, request, user_id)
        updated_project = await project_service.update_file_config(project_id, request, current_user_id)

        if not updated_project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在或无权限访问")

        # 构建响应数据
        response_data = create_project_response(updated_project)
        await _attach_project_detail_info(response_data, updated_project)

        return success_response(response_data, message=Messages.UPDATED_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新文件配置失败: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="文件配置更新失败")
