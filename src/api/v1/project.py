"""项目管理接口"""
from typing import Any, Optional, Union

from fastapi import APIRouter, Depends, Form, File, Query, status, HTTPException, Body, Request, UploadFile
from loguru import logger

from src.core.security.auth import get_current_user_id, get_current_user
from src.core.exceptions import ProjectNotFoundException
from src.core.response import success as success_response, page as page_response, Messages
from src.models.enums import ProjectType
from src.models.project import Project
from src.schemas.common import BaseResponse, PaginationResponse
from src.services.audit import audit_service
from src.models.audit_log import AuditAction
from src.schemas.project import (
    ProjectCreateRequest, ProjectRuleCreateRequest, ProjectFileCreateRequest, ProjectCodeCreateRequest,
    ProjectResponse, ProjectListResponse, ProjectCreateFormRequest, ProjectListQueryRequest, TaskJsonRequest,
    FileStructureResponse, FileContentResponse, ProjectFileContentUpdateRequest
)
from src.schemas.project_unified import UnifiedProjectUpdateRequest
from src.services.projects.project_service import project_service
from src.services.projects.unified_project_service import unified_project_service
from src.services.projects.relation_service import relation_service
from src.core.response import ProjectResponseBuilder
from src.utils.api_optimizer import fast_response, monitor_performance, optimize_large_response

project_router = APIRouter()


def create_project_response(project: Project) -> ProjectResponse:
    """构建项目响应（兼容旧代码）"""
    return ProjectResponseBuilder.build_detail(project)


async def get_project_create_form(
    name: str = Form(..., min_length=3, max_length=50),
    description: Optional[str] = Form(None, max_length=500),
    type: str = Form(...),
    tags: Optional[str] = Form(None),
    dependencies: Optional[str] = Form(None),
    venv_scope: str = Form(...),
    shared_venv_key: Optional[str] = Form(None),
    interpreter_source: str = Form("mise"),
    python_version: Optional[str] = Form(None),
    python_bin: Optional[str] = Form(None),
    # 节点环境参数
    env_location: str = Form("local"),
    node_id: Optional[str] = Form(None),
    use_existing_env: Optional[Union[bool, str]] = Form(None),
    existing_env_name: Optional[str] = Form(None),
    env_name: Optional[str] = Form(None),
    env_description: Optional[str] = Form(None),
    # 文件项目参数
    entry_point: Optional[str] = Form(None, max_length=255),
    runtime_config: Optional[str] = Form(None),
    environment_vars: Optional[str] = Form(None),
    engine: str = Form("requests"),
    target_url: Optional[str] = Form(None, max_length=2000),
    url_pattern: Optional[str] = Form(None, max_length=500),
    request_method: str = Form("GET"),
    callback_type: str = Form("list"),
    extraction_rules: Optional[str] = Form(None),
    pagination_config: Optional[str] = Form(None),
    max_pages: int = Form(10, ge=1, le=1000),
    start_page: int = Form(1, ge=1),
    request_delay: int = Form(1000, ge=0),
    priority: int = Form(0),
    headers: Optional[str] = Form(None),
    cookies: Optional[str] = Form(None),
    language: str = Form("python", max_length=50),
    version: str = Form("1.0.0", max_length=20),
    code_entry_point: Optional[str] = Form(None, max_length=255),
    documentation: Optional[str] = Form(None),
    code_content: Optional[str] = Form(None),
) -> ProjectCreateFormRequest:
    # 处理 use_existing_env 布尔值
    use_existing_env_bool = False
    if use_existing_env is not None:
        if isinstance(use_existing_env, bool):
            use_existing_env_bool = use_existing_env
        elif isinstance(use_existing_env, str):
            use_existing_env_bool = use_existing_env.lower() in ('true', '1', 'yes')

    return ProjectCreateFormRequest(
        name=name,
        description=description,
        type=type,
        tags=tags,
        dependencies=dependencies,
        venv_scope=venv_scope,
        python_version=python_version,
        shared_venv_key=shared_venv_key,
        interpreter_source=interpreter_source,
        python_bin=python_bin,
        env_location=env_location,
        node_id=node_id,
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
    type: str = Query(None),
    status: str = Query(None),
    tag: str = Query(None),
    created_by: str = Query(None),
    search: str = Query(None),
    node_id: str = Query(None, description="节点ID筛选"),
):
    return ProjectListQueryRequest(
        page=page,
        size=size,
        type=type,
        status=status,
        tag=tag,
        created_by=created_by,
        search=search,
        node_id=node_id,
    )


@project_router.post(
    "",
    response_model=BaseResponse[ProjectResponse],
    status_code=status.HTTP_201_CREATED,
    summary="创建项目",
    description="创建新项目，支持文件、规则、代码三种类型",
    response_description="返回创建的项目信息"
)
async def create_project(
    http_request: Request,
    form_data=Depends(get_project_create_form),
    file=File(None),
    files=File(None),
    code_file=File(None),
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user)
):
    """创建项目"""

    # 构建请求数据
    request_data = {
        "name": form_data.name,
        "description": form_data.description,
        "type": form_data.type,
        "tags": form_data.tags,
        "dependencies": form_data.dependencies,
        "venv_scope": form_data.venv_scope,
        "python_version": form_data.python_version,
        "shared_venv_key": form_data.shared_venv_key,
        "interpreter_source": form_data.interpreter_source,
        "python_bin": form_data.python_bin,
        # 节点环境参数
        "env_location": form_data.env_location,
        "node_id": form_data.node_id,
        "use_existing_env": form_data.use_existing_env,
        "existing_env_name": form_data.existing_env_name,
        "env_name": form_data.env_name,
        "env_description": form_data.env_description,
    }

    # 根据项目类型添加特定参数
    if form_data.type == ProjectType.FILE:
        request_data.update({
            "entry_point": form_data.entry_point,
            "runtime_config": form_data.runtime_config,
            "environment_vars": form_data.environment_vars,
        })
    elif form_data.type == ProjectType.RULE:
        # 验证规则项目必需字段
        if not form_data.target_url:
            raise HTTPException(status_code=400, detail="规则项目必须提供target_url")
        if not form_data.extraction_rules:
            raise HTTPException(status_code=400, detail="规则项目必须提供extraction_rules")

        request_data.update({
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
        })
    elif form_data.type == ProjectType.CODE:
        request_data.update({
            "language": form_data.language,
            "version": form_data.version,
            "entry_point": form_data.code_entry_point,
            "documentation": form_data.documentation,
            "code_content": form_data.code_content,
        })

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
        code_file=code_file
    )

    # 构建响应数据
    response_data = create_project_response(project)

    # 如果是文件项目，添加文件信息
    if project.type == ProjectType.FILE and hasattr(project, 'file_detail'):
        file_detail = project.file_detail
        response_data.file_info = {
            "original_name": file_detail.original_name,
            "file_size": file_detail.file_size,
            "file_hash": file_detail.file_hash
        }

    # 记录审计日志
    from src.services.users.user_service import user_service
    user = await user_service.get_user_by_id(current_user.user_id)
    await audit_service.log_project_action(
        action=AuditAction.PROJECT_CREATE,
        username=user.username if user else "unknown",
        project_id=project.id,
        project_name=project.name,
        user_id=current_user.user_id,
        ip_address=http_request.client.host if http_request.client else None,
        description=f"创建项目: {project.name} (类型: {project.type})"
    )

    return success_response(response_data, message=Messages.CREATED_SUCCESS, code=201)


@project_router.get(
    "",
    response_model=PaginationResponse[ProjectListResponse],
    summary="获取项目列表",
    description="获取当前用户的项目列表，支持分页和筛选"
)
@fast_response(cache_ttl=120, namespace="project:list")
@monitor_performance(slow_threshold=0.5)
@optimize_large_response(chunk_size=50)
async def get_projects_list(
    query_params=Depends(get_project_list_query),
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user)
):
    """获取项目列表"""
    from src.services.users.user_service import user_service

    is_admin = await user_service.is_admin(current_user.user_id)
    user_filter = None if is_admin else current_user_id
    if is_admin and query_params.created_by is not None:
        try:
            user_filter = int(query_params.created_by)
        except (ValueError, TypeError):
            from src.models import User
            user = await User.filter(public_id=query_params.created_by).first()
            user_filter = user.id if user else None

    projects, total = await project_service.get_projects_list(
        page=query_params.page,
        size=query_params.size,
        project_type=query_params.type,
        status=query_params.status.value if query_params.status else None,
        tag=query_params.tag,
        user_id=user_filter,
        search=query_params.search
    )

    return page_response(
        items=ProjectResponseBuilder.build_list(projects),
        total=total,
        page=query_params.page,
        size=query_params.size,
        message=Messages.QUERY_SUCCESS
    )


@project_router.get(
    "/{project_id}",
    response_model=BaseResponse[ProjectResponse],
    summary="获取项目详情"
)
@fast_response(cache_ttl=120, namespace="project:detail", key_prefix_fn=lambda args, kwargs: str(kwargs.get('project_id', args[0] if args else '')))
async def get_project_detail(
    project_id: str,
    current_user_id: int = Depends(get_current_user_id)
):
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
                "original_name": detail.original_name, "file_size": detail.file_size,
                "file_hash": detail.file_hash, "file_path": detail.file_path,
                "file_type": detail.file_type, "storage_type": detail.storage_type,
                "entry_point": detail.entry_point, "runtime_config": detail.runtime_config,
                "environment_vars": detail.environment_vars, "is_compressed": detail.is_compressed,
                "original_file_path": detail.original_file_path
            }
    elif project.type == ProjectType.RULE:
        if detail := await relation_service.get_project_rule_detail(project.id):
            response_data.rule_info = {
                "engine": detail.engine, "target_url": detail.target_url,
                "url_pattern": detail.url_pattern, "callback_type": detail.callback_type,
                "request_method": detail.request_method, "extraction_rules": detail.extraction_rules,
                "data_schema": detail.data_schema, "pagination_config": detail.pagination_config,
                "max_pages": detail.max_pages, "start_page": detail.start_page,
                "request_delay": detail.request_delay, "retry_count": detail.retry_count,
                "timeout": detail.timeout, "priority": getattr(detail, 'priority', 0),
                "dont_filter": getattr(detail, 'dont_filter', False), "headers": detail.headers,
                "cookies": detail.cookies, "proxy_config": detail.proxy_config,
                "task_config": getattr(detail, 'task_config', None)
            }
    elif project.type == ProjectType.CODE:
        if detail := await relation_service.get_project_code_detail(project.id):
            response_data.code_info = {
                "content": detail.content, "language": detail.language,
                "version": detail.version, "content_hash": detail.content_hash,
                "entry_point": detail.entry_point, "runtime_config": detail.runtime_config,
                "environment_vars": detail.environment_vars
            }



@project_router.put(
    "/{project_id}",
    response_model=BaseResponse[ProjectResponse],
    summary="更新项目"
)
async def update_project(
    project_id: str,
    request: UnifiedProjectUpdateRequest,
    current_user_id: int = Depends(get_current_user_id)
):
    """统一更新项目"""
    project = await unified_project_service.update_project_unified(project_id, request, current_user_id)
    if not project:
        raise ProjectNotFoundException(project_id)

    response_data = create_project_response(project)
    await _attach_project_detail_info(response_data, project)
    return success_response(response_data, message=Messages.UPDATED_SUCCESS)


@project_router.delete(
    "/{project_id}",
    response_model=BaseResponse[None],
    summary="删除项目",
    description="删除指定的项目（不可逆操作）",
    response_description="返回删除操作结果"
)
async def delete_project(
    project_id,
    http_request: Request,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user)
):
    # 获取项目信息用于审计
    project = await project_service.get_project_by_id(project_id, current_user_id)
    project_name = project.name if project else project_id

    # 删除项目
    deleted = await project_service.delete_project(project_id, current_user_id)
    if not deleted:
        raise ProjectNotFoundException(project_id)

    # 记录审计日志
    from src.services.users.user_service import user_service
    user = await user_service.get_user_by_id(current_user.user_id)
    await audit_service.log_project_action(
        action=AuditAction.PROJECT_DELETE,
        username=user.username if user else "unknown",
        project_id=project.id if project else 0,
        project_name=project_name,
        user_id=current_user.user_id,
        ip_address=http_request.client.host if http_request.client else None,
        description=f"删除项目: {project_name}"
    )

    return success_response(None, message=Messages.DELETED_SUCCESS)


@project_router.post(
    "/batch-delete",
    response_model=BaseResponse[dict],
    summary="批量删除项目",
    description="批量删除多个项目（不可逆操作）",
    response_description="返回批量删除操作结果"
)
@fast_response(background_execution=True)  # 后台执行
@monitor_performance(slow_threshold=2.0)  # 监控超过2秒的批量操作
async def batch_delete_projects(
    request=Body(...),
    current_user_id=Depends(get_current_user_id)
):
    """批量删除项目"""
    project_ids = request.get("project_ids", [])
    if not project_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="项目ID列表不能为空"
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
        except Exception as e:
            failed_count += 1
            failed_projects.append(project_id)

    result_data = {
        "total": len(project_ids),
        "success_count": success_count,
        "failed_count": failed_count,
        "failed_projects": failed_projects
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
    response_description="返回任务JSON配置"
)
async def generate_task_json(
    project_id,
    current_user_id=Depends(get_current_user_id)
):
    """生成任务JSON"""

    # 获取项目详情
    project = await project_service.get_project_detail(project_id, current_user_id)
    if not project:
        raise ProjectNotFoundException(project_id)

    # 检查项目类型
    if project.type != ProjectType.RULE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="只有规则项目才能生成任务JSON"
        )

    # 获取规则详情 - 使用应用层关联
    from src.services.projects.relation_service import relation_service
    rule_detail = await relation_service.get_project_rule_detail(project.id)
    if not rule_detail:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="规则项目配置不完整"
        )

    # 构建任务JSON
    task_json = await project_service.generate_task_json(rule_detail)

    return success_response(task_json, message="任务JSON生成成功")


@project_router.put(
    "/{project_id}/rule-config",
    response_model=BaseResponse[ProjectResponse],
    summary="更新规则项目配置",
    description="更新规则项目的详细配置",
    response_description="返回更新后的项目信息"
)
async def update_rule_config(
    project_id,
    request,
    current_user_id=Depends(get_current_user_id)
):
    """更新规则项目配置"""

    # 获取项目详情
    project = await project_service.get_project_by_id(project_id, current_user_id)
    if not project:
        raise ProjectNotFoundException(project_id)

    # 检查项目类型
    if project.type != ProjectType.RULE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="只有规则项目才能更新规则配置"
        )

    # 更新规则配置
    updated_project = await project_service.update_rule_config(project_id, request, current_user_id)

    # 构建响应数据
    response_data = create_project_response(updated_project)

    return success_response(response_data, message=Messages.UPDATED_SUCCESS)


@project_router.put("/{project_id}/code-config", response_model=BaseResponse[ProjectResponse])
async def update_code_config(
    project_id,
    request,
    current_user_id=Depends(get_current_user_id)
):
    """更新代码项目配置"""
    try:
        # 更新代码配置
        updated_project = await project_service.update_code_config(project_id, request, current_user_id)

        if not updated_project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目不存在或无权限访问"
            )

        # 构建响应数据
        response_data = create_project_response(updated_project)

        return success_response(response_data, message=Messages.UPDATED_SUCCESS)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新代码项目配置失败: {str(e)}"
        )


@project_router.put(
    "/{project_id}/file-config",
    response_model=BaseResponse[ProjectResponse],
    summary="更新文件项目配置",
    description="更新文件项目的详细配置，支持文件替换",
    response_description="返回更新后的项目信息"
)
async def update_file_config(
    project_id,
    entry_point=Form(None),
    runtime_config=Form(None),
    environment_vars=Form(None),
    file=File(None),
    current_user_id=Depends(get_current_user_id)
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
                detail="只有文件项目才能更新文件配置"
            )

        # 构建更新请求
        from src.schemas.project import ProjectFileUpdateRequest

        # 解析JSON字段
        from src.utils.json_parser import JSONParser
        parsed_runtime_config = JSONParser.parse_safely(runtime_config, "runtime_config")
        parsed_environment_vars = JSONParser.parse_safely(environment_vars, "environment_vars")

        request = ProjectFileUpdateRequest(
            entry_point=entry_point,
            runtime_config=parsed_runtime_config,
            environment_vars=parsed_environment_vars
        )

        # 更新文件配置
        updated_project = await project_service.update_file_config(
            project_id, request, current_user_id, file
        )

        if not updated_project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目不存在或无权限访问"
            )

        # 构建响应数据
        response_data = create_project_response(updated_project)

        return success_response(response_data, message=Messages.UPDATED_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新文件配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="文件配置更新失败"
        )


@project_router.get(
    "/{project_id}/files/structure",
    response_model=BaseResponse[FileStructureResponse],
    summary="获取项目文件结构",
    description="获取项目的文件结构树，包括解压后的文件组织",
    response_description="返回项目文件结构树"
)
async def get_project_file_structure(
    project_id,
    current_user_id=Depends(get_current_user_id)
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
                detail="只有文件项目支持文件结构查看"
            )

        # 获取文件详情
        from src.services.projects.relation_service import relation_service
        file_detail = await relation_service.get_project_file_detail(project.id)
        if not file_detail:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目文件详情不存在"
            )

        # 获取文件结构
        from src.services.projects.project_file_service import project_file_service
        structure = await project_file_service.get_project_file_structure(file_detail.file_path)

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
            total_size=total_size
        )

        return success_response(response_data, message=Messages.QUERY_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取项目文件结构失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取项目文件结构失败: {str(e)}"
        )


@project_router.get(
    "/{project_id}/files/content",
    response_model=BaseResponse[FileContentResponse],
    summary="获取项目文件内容",
    description="获取项目中特定文件的内容",
    response_description="返回文件内容"
)
async def get_project_file_content(
    project_id,
    file_path=Query(...),
    current_user_id=Depends(get_current_user_id)
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
                detail="只有文件项目支持文件内容查看"
            )

        # 获取文件详情
        from src.services.projects.relation_service import relation_service
        file_detail = await relation_service.get_project_file_detail(project.id)
        if not file_detail:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目文件详情不存在"
            )

        # 获取文件内容
        from src.services.projects.project_file_service import project_file_service
        file_content = await project_file_service.get_file_content(
            file_detail.file_path, 
            file_path
        )

        return success_response(FileContentResponse(**file_content), message=Messages.QUERY_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取项目文件内容失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取项目文件内容失败: {str(e)}"
        )


@project_router.put(
    "/{project_id}/files/content",
    response_model=BaseResponse[FileContentResponse],
    summary="更新项目文件内容",
    description="更新项目中文件的内容",
    response_description="返回更新后的文件内容"
)
async def update_project_file_content(
    project_id: str,
    payload: ProjectFileContentUpdateRequest,
    current_user_id: int = Depends(get_current_user_id)
):
    """更新项目文件内容"""
    try:
        project = await project_service.get_project_by_id(project_id, current_user_id)
        if not project:
            raise ProjectNotFoundException(project_id)

        if project.type != ProjectType.FILE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有文件项目支持文件内容编辑"
            )

        from src.services.projects.relation_service import relation_service

        file_detail = await relation_service.get_project_file_detail(project.id)
        if not file_detail:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目文件详情不存在"
            )

        from src.services.projects.project_file_service import project_file_service

        updated = await project_file_service.update_file_content(
            file_detail.file_path,
            payload.file_path,
            payload.content,
            payload.encoding
        )

        # 标记项目过期（用于分布式同步）
        try:
            from src.models import ProjectFile
            from src.services.nodes.node_project_service import node_project_service
            from datetime import datetime

            await ProjectFile.filter(id=file_detail.id).update(
                is_modified=True,
                last_modified_at=datetime.now()
            )

            await node_project_service.mark_project_outdated(project.public_id)

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
            detail=f"更新项目文件内容失败: {str(e)}"
        )


@project_router.get(
    "/{project_id}/files/download",
    summary="下载项目文件",
    description="下载项目的原始文件或解压后的特定文件",
    response_description="返回文件下载"
)
async def download_project_file(
    project_id,
    file_path=Query(None),
    current_user_id=Depends(get_current_user_id)
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
                detail="只有文件项目支持文件下载"
            )

        # 获取文件详情
        from src.services.projects.relation_service import relation_service
        file_detail = await relation_service.get_project_file_detail(project.id)
        if not file_detail:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目文件详情不存在"
            )

        # 下载文件
        from src.services.projects.project_file_service import project_file_service

        if file_path:
            # 下载解压后的特定文件
            return await project_file_service.download_file(
                file_detail.file_path, 
                file_path
            )
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
            detail=f"下载项目文件失败: {str(e)}"
        )
