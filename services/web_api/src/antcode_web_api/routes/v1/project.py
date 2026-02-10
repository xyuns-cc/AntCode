"""项目管理接口"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from antcode_web_api.response import (
    ExecutionResponseBuilder,
    Messages,
    ProjectResponseBuilder,
    page as page_response,
    success as success_response,
)
from antcode_core.common.security.auth import get_current_user, get_current_user_id
from antcode_core.common.utils.api_optimizer import (
    fast_response,
    monitor_performance,
    optimize_large_response,
)
from antcode_core.common.utils.json_parser import JSONParser
from antcode_core.domain.models import Task, TaskRun, User
from antcode_core.domain.models.audit_log import AuditAction
from antcode_core.domain.models.enums import ProjectType
from antcode_core.domain.models.project import Project, ProjectCode, ProjectFile, ProjectRule
from antcode_core.domain.schemas.common import BaseResponse, PaginationResponse
from antcode_core.domain.schemas.project import (
    FileContentResponse,
    FileStructureResponse,
    ProjectCodeCreateRequest,
    ProjectCreateFormRequest,
    ProjectCreateRequest,
    ProjectFileContentUpdateRequest,
    ProjectFileCreateRequest,
    ProjectFileUpdateRequest,
    ProjectListQueryRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectRuleCreateRequest,
    TaskJsonRequest,
)
from antcode_core.domain.schemas.project_unified import UnifiedProjectUpdateRequest
from antcode_core.application.services.audit import audit_service
from antcode_core.application.services.workers.worker_project_service import (
    worker_project_service,
)
from antcode_web_api.services.projects.project_file_service import project_file_service
from antcode_core.application.services.projects.project_service import project_service
from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.application.services.projects.unified_project_service import unified_project_service
from antcode_core.application.services.users.user_service import user_service
from antcode_web_api.exceptions import ProjectNotFoundException

project_router = APIRouter()


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
    name: str | None = None
    type: str | None = None
    runtime_scope: str | None = None
    python_version: str | None = None
    target_url: str | None = None
    extraction_rules: str | dict | None = None
    code_content: str | None = None
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
    if any(c in text for c in [":", "-", "#", "\n", "\"", "'"]):
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


async def get_project_create_form(
    name: str = Form(..., min_length=3, max_length=50),
    description: str | None = Form(None, max_length=500),
    project_type: str = Form(..., alias="type"),
    tags: str | None = Form(None),
    dependencies: str | None = Form(None),
    runtime_scope: str = Form(...),
    shared_runtime_key: str | None = Form(None),
    interpreter_source: str = Form("mise"),
    python_version: str | None = Form(None),
    python_bin: str | None = Form(None),
    # Worker 环境参数
    env_location: str = Form("worker"),
    worker_id: str | None = Form(None),
    use_existing_env: bool | str | None = Form(None),
    existing_env_name: str | None = Form(None),
    env_name: str | None = Form(None),
    env_description: str | None = Form(None),
    # 文件项目参数
    entry_point: str | None = Form(None, max_length=255),
    runtime_config: str | None = Form(None),
    environment_vars: str | None = Form(None),
    engine: str = Form("requests"),
    target_url: str | None = Form(None, max_length=2000),
    url_pattern: str | None = Form(None, max_length=500),
    request_method: str = Form("GET"),
    callback_type: str = Form("list"),
    extraction_rules: str | None = Form(None),
    pagination_config: str | None = Form(None),
    max_pages: int = Form(10, ge=1, le=1000),
    start_page: int = Form(1, ge=1),
    request_delay: int = Form(1000, ge=0),
    priority: int = Form(0),
    headers: str | None = Form(None),
    cookies: str | None = Form(None),
    language: str = Form("python", max_length=50),
    version: str = Form("1.0.0", max_length=20),
    code_entry_point: str | None = Form(None, max_length=255),
    documentation: str | None = Form(None),
    code_content: str | None = Form(None),
) -> ProjectCreateFormRequest:
    # 处理 use_existing_env 布尔值
    use_existing_env_bool = False
    if use_existing_env is not None:
        if isinstance(use_existing_env, bool):
            use_existing_env_bool = use_existing_env
        elif isinstance(use_existing_env, str):
            use_existing_env_bool = use_existing_env.lower() in ("true", "1", "yes")

    return ProjectCreateFormRequest(
        name=name,
        description=description,
        type=project_type,
        tags=tags,
        dependencies=dependencies,
        runtime_scope=runtime_scope,
        python_version=python_version,
        shared_runtime_key=shared_runtime_key,
        interpreter_source=interpreter_source,
        python_bin=python_bin,
        env_location=env_location,
        worker_id=worker_id,
        use_existing_env=use_existing_env_bool,
        existing_env_name=existing_env_name,
        env_name=env_name,
        env_description=env_description,
        entry_point=entry_point,
        runtime_config=runtime_config,
        environment_vars=environment_vars,
        engine=engine,
        target_url=target_url,
        url_pattern=url_pattern,
        request_method=request_method,
        callback_type=callback_type,
        extraction_rules=extraction_rules,
        pagination_config=pagination_config,
        max_pages=max_pages,
        start_page=start_page,
        request_delay=request_delay,
        priority=priority,
        headers=headers,
        cookies=cookies,
        language=language,
        version=version,
        code_entry_point=code_entry_point,
        documentation=documentation,
        code_content=code_content,
    )


async def get_project_list_query(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=500),
    project_type: str | None = Query(None, alias="type"),
    status: str = Query(None),
    tag: str = Query(None),
    created_by: str = Query(None),
    search: str = Query(None),
    worker_id: str = Query(None, description="Worker ID 筛选"),
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
    file=File(None),
    files=File(None),
    code_file=File(None),
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """创建项目"""

    # 构建请求数据
    request_data = {
        "name": form_data.name,
        "description": form_data.description,
        "type": form_data.type,
        "tags": form_data.tags,
        "dependencies": form_data.dependencies,
        "runtime_scope": form_data.runtime_scope,
        "python_version": form_data.python_version,
        "shared_runtime_key": form_data.shared_runtime_key,
        "interpreter_source": form_data.interpreter_source,
        "python_bin": form_data.python_bin,
        # Worker 环境参数
        "env_location": form_data.env_location,
        "worker_id": form_data.worker_id,
        "use_existing_env": form_data.use_existing_env,
        "existing_env_name": form_data.existing_env_name,
        "env_name": form_data.env_name,
        "env_description": form_data.env_description,
    }

    # 根据项目类型添加特定参数
    if form_data.type == ProjectType.FILE:
        request_data.update(
            {
                "entry_point": form_data.entry_point,
                "runtime_config": form_data.runtime_config,
                "environment_vars": form_data.environment_vars,
            }
        )
    elif form_data.type == ProjectType.RULE:
        # 验证规则项目必需字段
        if not form_data.target_url:
            raise HTTPException(status_code=400, detail="规则项目必须提供target_url")
        if not form_data.extraction_rules:
            raise HTTPException(status_code=400, detail="规则项目必须提供extraction_rules")

        request_data.update(
            {
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
                "priority": form_data.priority,
                "headers": form_data.headers,
                "cookies": form_data.cookies,
            }
        )
    elif form_data.type == ProjectType.CODE:
        request_data.update(
            {
                "language": form_data.language,
                "version": form_data.version,
                "entry_point": form_data.code_entry_point,
                "documentation": form_data.documentation,
                "code_content": form_data.code_content,
            }
        )

    # 根据项目类型创建不同的请求对象
    if form_data.type == ProjectType.RULE:
        request = ProjectRuleCreateRequest(**request_data)
    elif form_data.type == ProjectType.FILE:
        request = ProjectFileCreateRequest(**request_data)
    elif form_data.type == ProjectType.CODE:
        request = ProjectCodeCreateRequest(**request_data)
    else:
        request = ProjectCreateRequest(**request_data)

    # 创建项目
    project = await project_service.create_project(
        request=request,
        user_id=current_user_id,
        file=file,
        files=files,
        code_file=code_file,
    )

    # 构建响应数据
    response_data = create_project_response(project)

    # 如果是文件项目，添加文件信息
    if project.type == ProjectType.FILE and hasattr(project, "file_detail"):
        file_detail = project.file_detail
        response_data.file_info = {
            "original_name": file_detail.original_name,
            "file_size": file_detail.file_size,
            "file_hash": file_detail.file_hash,
        }

    # 记录审计日志
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

    return success_response(response_data, message=Messages.CREATED_SUCCESS, code=201)


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
    errors: list[str] = []

    project_type = payload.type
    if project_type and isinstance(project_type, str):
        project_type = project_type.lower()

    if not payload.name:
        errors.append("项目名称不能为空")
    if not payload.type:
        errors.append("项目类型不能为空")
    if not payload.runtime_scope:
        errors.append("运行时作用域不能为空")
    if not payload.python_version:
        errors.append("Python 版本不能为空")

    if project_type == ProjectType.RULE or project_type == ProjectType.RULE.value:
        if not payload.target_url:
            errors.append("规则项目必须提供 target_url")
        if not payload.extraction_rules:
            errors.append("规则项目必须提供 extraction_rules")
    elif project_type == ProjectType.CODE or project_type == ProjectType.CODE.value:
        if not payload.code_content:
            errors.append("代码项目必须提供 code_content")

    return success_response(
        {"valid": len(errors) == 0, "errors": errors if errors else None},
        message=Messages.QUERY_SUCCESS,
    )


@project_router.post(
    "/import",
    response_model=BaseResponse[list[ProjectResponse]],
    summary="导入文件项目",
)
async def import_projects(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    description: str | None = Form(None),
    entry_point: str | None = Form(None),
    runtime_config: str | None = Form(None),
    environment_vars: str | None = Form(None),
    overwrite_existing: bool = Form(False),
    runtime_scope: str = Form(...),
    worker_id: str = Form(...),
    use_existing_env: bool | str | None = Form(None),
    existing_env_name: str | None = Form(None),
    python_version: str | None = Form(None),
    shared_runtime_key: str | None = Form(None),
    env_name: str | None = Form(None),
    env_description: str | None = Form(None),
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """导入文件项目（上传文件/压缩包）"""
    raw_name = (name or Path(file.filename or "").stem or "imported-project").strip()
    base_name = raw_name or "imported-project"

    use_base_name = True
    if overwrite_existing:
        existing = await Project.get_or_none(name=base_name, user_id=current_user_id)
        if existing:
            await project_service.delete_project(existing.public_id, current_user_id)
        else:
            if await Project.filter(name=base_name).exists():
                use_base_name = False

    if use_base_name:
        project_name = base_name
    else:
        project_name = await _generate_unique_project_name(base_name)

    use_existing_env_bool = False
    if use_existing_env is not None:
        if isinstance(use_existing_env, bool):
            use_existing_env_bool = use_existing_env
        elif isinstance(use_existing_env, str):
            use_existing_env_bool = use_existing_env.lower() in ("true", "1", "yes")

    request_data = {
        "name": project_name,
        "description": description or "",
        "type": ProjectType.FILE,
        "tags": [],
        "dependencies": None,
        "runtime_scope": runtime_scope,
        "python_version": python_version,
        "shared_runtime_key": shared_runtime_key,
        "interpreter_source": "mise",
        "python_bin": None,
        "env_location": "worker",
        "worker_id": worker_id,
        "use_existing_env": use_existing_env_bool,
        "existing_env_name": existing_env_name,
        "env_name": env_name,
        "env_description": env_description,
        "entry_point": entry_point,
        "runtime_config": runtime_config,
        "environment_vars": environment_vars,
    }
    request = ProjectFileCreateRequest(**request_data)
    new_project = await project_service.create_project(
        request=request,
        user_id=current_user_id,
        file=file,
        files=None,
        code_file=None,
    )

    response_data = create_project_response(new_project)
    await _attach_project_detail_info(response_data, new_project)
    return success_response([response_data], message=Messages.CREATED_SUCCESS)


@project_router.post(
    "/{project_id}/export",
    summary="导出项目配置",
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

    payload = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "project": project_payload,
    }

    include_tasks = bool(request.include_tasks)
    include_logs = bool(request.include_logs)

    task_items: list[dict[str, object]] = []
    if include_tasks:
        is_admin = await user_service.is_admin(current_user.user_id)
        task_query = Task.filter(project_id=project.id)
        if not is_admin:
            task_query = task_query.filter(user_id=current_user_id)
        tasks = await task_query.all()
        task_items = [_task_export_payload(task, project.public_id) for task in tasks]
        payload["tasks"] = task_items

    if include_logs and task_items:
        task_ids = await Task.filter(project_id=project.id).values_list("id", flat=True)
        run_query = TaskRun.filter(task_id__in=list(task_ids))
        if request.date_range:
            start = request.date_range.get("start")
            end = request.date_range.get("end")
            if start:
                try:
                    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    run_query = run_query.filter(created_at__gte=start_dt)
                except ValueError:
                    raise HTTPException(status_code=400, detail="date_range.start 格式错误")
            if end:
                try:
                    end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                    run_query = run_query.filter(created_at__lte=end_dt)
                except ValueError:
                    raise HTTPException(status_code=400, detail="date_range.end 格式错误")
        runs = await run_query.order_by("-created_at").limit(200)
        payload["executions"] = ExecutionResponseBuilder.build_list(runs)

    fmt = (request.format or "json").lower()
    if fmt == "yaml":
        content = _yaml_dump(payload)
        media_type = "text/yaml"
        filename = f"project_{project.public_id}.yaml"
    elif fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        if include_tasks and task_items:
            writer.writerow(["task_id", "name", "schedule_type", "is_active", "status", "project_id"])
            for item in task_items:
                writer.writerow([
                    "",
                    item.get("name"),
                    item.get("schedule_type"),
                    item.get("is_active"),
                    "",
                    item.get("project_id"),
                ])
        else:
            writer.writerow(["project_id", "name", "type", "status", "description", "tags"])
            writer.writerow([
                project_payload.get("id"),
                project_payload.get("name"),
                project_payload.get("type"),
                project_payload.get("status"),
                project_payload.get("description"),
                ",".join(project_payload.get("tags") or []),
            ])
        content = output.getvalue()
        media_type = "text/csv"
        filename = f"project_{project.public_id}.csv"
    else:
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        media_type = "application/json"
        filename = f"project_{project.public_id}.json"

    buffer = io.BytesIO(content.encode("utf-8"))
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(buffer, media_type=media_type, headers=headers)


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


async def _attach_project_detail_info(response_data: ProjectResponse, project):
    """为项目响应附加详细信息"""
    if project.type == ProjectType.FILE:
        if detail := await relation_service.get_project_file_detail(project.id):
            response_data.file_info = {
                "original_name": detail.original_name,
                "file_size": detail.file_size,
                "file_hash": detail.file_hash,
                "file_path": detail.file_path,
                "file_type": detail.file_type,
                "storage_type": detail.storage_type,
                "entry_point": detail.entry_point,
                "runtime_config": detail.runtime_config,
                "environment_vars": detail.environment_vars,
                "is_compressed": detail.is_compressed,
                "original_file_path": detail.original_file_path,
            }
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
                "headers": detail.headers,
                "cookies": detail.cookies,
                "proxy_config": detail.proxy_config,
                "task_config": getattr(detail, "task_config", None),
            }
    elif project.type == ProjectType.CODE and (detail := await relation_service.get_project_code_detail(project.id)):
        response_data.code_info = {
                "content": detail.content,
                "language": detail.language,
                "version": detail.version,
                "content_hash": detail.content_hash,
                "entry_point": detail.entry_point,
                "runtime_config": detail.runtime_config,
                "environment_vars": detail.environment_vars,
            }


@project_router.put(
    "/{project_id}", response_model=BaseResponse[ProjectResponse], summary="更新项目"
)
async def update_project(
    project_id: str,
    request: UnifiedProjectUpdateRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    """统一更新项目"""
    project = await unified_project_service.update_project_unified(
        project_id, request, current_user_id
    )
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
    """复制项目"""
    project = await project_service.get_project_by_id(project_id, current_user_id)
    if not project:
        raise ProjectNotFoundException(project_id)

    base_name = payload.name or f"{project.name}-copy"
    name = await _generate_unique_project_name(base_name)

    new_project = await Project.create(
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
        fallback_enabled=project.fallback_enabled,
        user_id=current_user_id,
        updated_by=current_user_id,
    )

    if project.type == ProjectType.FILE:
        detail = await relation_service.get_project_file_detail(project.id)
        if detail:
            await ProjectFile.create(
                project_id=new_project.id,
                file_path=detail.file_path,
                original_file_path=detail.original_file_path,
                original_name=detail.original_name,
                file_size=detail.file_size,
                file_type=detail.file_type,
                file_hash=detail.file_hash,
                entry_point=detail.entry_point,
                runtime_config=detail.runtime_config,
                environment_vars=detail.environment_vars,
                storage_type=detail.storage_type,
                is_compressed=detail.is_compressed,
                compression_ratio=detail.compression_ratio,
                file_count=detail.file_count,
                additional_files=detail.additional_files,
                draft_manifest_key=detail.draft_manifest_key,
                draft_root_prefix=detail.draft_root_prefix,
                dirty=detail.dirty,
                dirty_files_count=detail.dirty_files_count,
                last_editor_id=detail.last_editor_id,
                last_edit_at=detail.last_edit_at,
                published_version=detail.published_version,
            )
    elif project.type == ProjectType.RULE:
        detail = await relation_service.get_project_rule_detail(project.id)
        if detail:
            await ProjectRule.create(
                project_id=new_project.id,
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
    elif project.type == ProjectType.CODE:
        detail = await relation_service.get_project_code_detail(project.id)
        if detail:
            await ProjectCode.create(
                project_id=new_project.id,
                content=detail.content,
                language=detail.language,
                version=detail.version,
                content_hash=detail.content_hash,
                entry_point=detail.entry_point,
                runtime_config=detail.runtime_config,
                environment_vars=detail.environment_vars,
                documentation=detail.documentation,
                changelog=detail.changelog,
            )

    response_data = create_project_response(new_project)
    await _attach_project_detail_info(response_data, new_project)
    return success_response(response_data, message=Messages.CREATED_SUCCESS)


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
    project = await project_service.get_project_detail(project_id, current_user_id)
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
async def update_code_config(project_id, request, current_user_id=Depends(get_current_user_id)):
    """更新代码项目配置"""
    try:
        # 更新代码配置
        updated_project = await project_service.update_code_config(
            project_id, request, current_user_id
        )

        if not updated_project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在或无权限访问"
            )

        # 构建响应数据
        response_data = create_project_response(updated_project)

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
    project_id,
    entry_point=Form(None),
    runtime_config=Form(None),
    environment_vars=Form(None),
    file=File(None),
    current_user_id=Depends(get_current_user_id),
):
    """更新文件项目配置"""
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

        # 构建更新请求
        # 解析JSON字段
        parsed_runtime_config = JSONParser.parse_safely(runtime_config, "runtime_config")
        parsed_environment_vars = JSONParser.parse_safely(environment_vars, "environment_vars")

        request = ProjectFileUpdateRequest(
            entry_point=entry_point,
            runtime_config=parsed_runtime_config,
            environment_vars=parsed_environment_vars,
        )

        # 更新文件配置
        updated_project = await project_service.update_file_config(
            project_id, request, current_user_id, file
        )

        if not updated_project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在或无权限访问"
            )

        # 构建响应数据
        response_data = create_project_response(updated_project)

        return success_response(response_data, message=Messages.UPDATED_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新文件配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="文件配置更新失败"
        )


@project_router.get(
    "/{project_id}/files/structure",
    response_model=BaseResponse[FileStructureResponse],
    summary="获取项目文件结构",
    description="获取项目的文件结构树，支持版本参数（draft/latest/版本号）",
    response_description="返回项目文件结构树",
)
async def get_project_file_structure(
    project_id,
    version: str = Query("draft", description="版本标识: draft(草稿), latest(最新版本), 或版本号"),
    current_user_id=Depends(get_current_user_id),
):
    """获取项目文件结构"""
    try:
        # 获取项目详情
        project = await project_service.get_project_by_id(project_id, current_user_id)
        if not project:
            raise ProjectNotFoundException(project_id)

        # 只支持文件项目
        if project.type != ProjectType.FILE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有文件项目支持文件结构查看",
            )

        # 获取文件详情
        file_detail = await relation_service.get_project_file_detail(project.id)
        if not file_detail:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目文件详情不存在")

        # 获取文件结构（支持版本参数）
        structure = await project_file_service.get_versioned_file_structure(
            project.id, version
        )

        # 统计文件信息
        def count_files(node):
            if node.get("type") == "file":
                return 1
            elif node.get("children"):
                return sum(count_files(child) for child in node["children"])
            return 0

        def sum_size(node):
            return node.get("size", 0)

        total_files = count_files(structure)
        total_size = sum_size(structure)

        response_data = FileStructureResponse(
            project_id=project.public_id,
            project_name=project.name,
            file_path=file_detail.file_path,
            structure=structure,
            total_files=total_files,
            total_size=total_size,
        )

        return success_response(response_data, message=Messages.QUERY_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取项目文件结构失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取项目文件结构失败: {str(e)}",
        )


@project_router.get(
    "/{project_id}/files/content",
    response_model=BaseResponse[FileContentResponse],
    summary="获取项目文件内容",
    description="获取项目中特定文件的内容，支持版本参数（draft/latest/版本号）",
    response_description="返回文件内容",
)
async def get_project_file_content(
    project_id,
    file_path=Query(..., description="文件路径"),
    version: str = Query("draft", description="版本标识: draft(草稿), latest(最新版本), 或版本号"),
    current_user_id=Depends(get_current_user_id),
):
    """获取项目文件内容"""
    try:
        # 获取项目详情
        project = await project_service.get_project_by_id(project_id, current_user_id)
        if not project:
            raise ProjectNotFoundException(project_id)

        # 只支持文件项目
        if project.type != ProjectType.FILE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有文件项目支持文件内容查看",
            )

        # 获取文件详情
        file_detail = await relation_service.get_project_file_detail(project.id)
        if not file_detail:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目文件详情不存在")

        # 获取文件内容（支持版本参数）
        file_content = await project_file_service.get_versioned_file_content(
            project.id, file_path, version
        )

        return success_response(FileContentResponse(**file_content), message=Messages.QUERY_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取项目文件内容失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取项目文件内容失败: {str(e)}",
        )


@project_router.put(
    "/{project_id}/files/content",
    response_model=BaseResponse[FileContentResponse],
    summary="更新项目文件内容",
    description="更新项目中文件的内容",
    response_description="返回更新后的文件内容",
)
async def update_project_file_content(
    project_id: str,
    payload: ProjectFileContentUpdateRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    """更新项目文件内容"""
    try:
        project = await project_service.get_project_by_id(project_id, current_user_id)
        if not project:
            raise ProjectNotFoundException(project_id)

        if project.type != ProjectType.FILE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有文件项目支持文件内容编辑",
            )

        file_detail = await relation_service.get_project_file_detail(project.id)
        if not file_detail:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目文件详情不存在")

        updated = await project_file_service.update_file_content(
            file_detail.file_path, payload.file_path, payload.content, payload.encoding
        )

        # 标记项目过期（用于分布式同步）
        try:
            await ProjectFile.filter(id=file_detail.id).update(
                dirty=True,
                dirty_files_count=(file_detail.dirty_files_count or 0) + 1,
                last_editor_id=current_user_id,
                last_edit_at=datetime.now(),
            )

            await worker_project_service.mark_project_outdated(project.public_id)

            logger.debug(f"项目已标记过期 [{project.public_id}]")
        except Exception as mark_error:
            logger.warning(f"过期标记失败: {mark_error}")

        return success_response(FileContentResponse(**updated), message=Messages.UPDATED_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新项目文件内容失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新项目文件内容失败: {str(e)}",
        )


@project_router.get(
    "/{project_id}/files/download",
    summary="下载项目文件",
    description="下载项目的原始文件或解压后的特定文件",
    response_description="返回文件下载",
)
async def download_project_file(
    project_id, file_path=Query(None), current_user_id=Depends(get_current_user_id)
):
    """下载项目文件"""
    try:
        # 获取项目详情
        project = await project_service.get_project_by_id(project_id, current_user_id)
        if not project:
            raise ProjectNotFoundException(project_id)

        # 只支持文件项目
        if project.type != ProjectType.FILE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有文件项目支持文件下载",
            )

        # 获取文件详情
        file_detail = await relation_service.get_project_file_detail(project.id)
        if not file_detail:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目文件详情不存在")

        # 下载文件
        if file_path:
            # 下载解压后的特定文件
            return await project_file_service.download_file(file_detail.file_path, file_path)
        else:
            # 下载原始文件（如果是压缩包则下载压缩包，否则下载文件本身）
            if file_detail.is_compressed and file_detail.original_file_path:
                # 下载原始压缩包
                return await project_file_service.download_file(file_detail.original_file_path)
            else:
                # 下载单个文件
                return await project_file_service.download_file(file_detail.file_path)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下载项目文件失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"下载项目文件失败: {str(e)}",
        )
