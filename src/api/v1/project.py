"""
项目相关API接口
处理项目的创建、查询、更新、删除等操作
"""

from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, Form, File, UploadFile, Query, status, HTTPException, Body
from loguru import logger

from src.core.auth import get_current_user_id, get_current_user
from src.core.exceptions import ProjectNotFoundException
from src.models.enums import ProjectType, ProjectStatus
from src.schemas.common import BaseResponse, PaginationResponse, PaginationInfo
from src.core.response import success as success_response, page as page_response
from src.core.messages import Messages
from src.schemas.project import (
    ProjectCreateRequest, ProjectRuleCreateRequest, ProjectFileCreateRequest, ProjectCodeCreateRequest,
    ProjectResponse, ProjectListResponse, ProjectCreateFormRequest, ProjectListQueryRequest, TaskJsonRequest,
    ProjectRuleUpdateRequest,
    ProjectCodeUpdateRequest, FileStructureResponse, FileContentResponse
)
from src.schemas.project_unified import UnifiedProjectUpdateRequest
from src.services.projects.project_service import project_service
from src.services.projects.unified_project_service import unified_project_service
from src.utils.api_optimizer import fast_response, monitor_performance, optimize_large_response

project_router = APIRouter()


def create_project_response(project) -> ProjectResponse:
    """创建 ProjectResponse 对象"""
    return ProjectResponse.model_construct(
        id=project.id,
        name=project.name,
        description=project.description,
        type=project.type,
        status=project.status,
        tags=project.tags or [],
        dependencies=project.dependencies,
        created_at=project.created_at,
        updated_at=project.updated_at,
        created_by=getattr(project, 'created_by', getattr(project, 'user_id', None)),
        created_by_username=getattr(project, 'created_by_username', None),
        download_count=project.download_count,
        star_count=project.star_count,
        file_info=getattr(project, 'file_info', None),
        rule_info=getattr(project, 'rule_info', None),
        code_info=getattr(project, 'code_info', None),
        python_version=getattr(project, 'python_version', None),
        venv_scope=getattr(project, 'venv_scope', None),
        venv_path=getattr(project, 'venv_path', None),
    )


async def get_project_create_form(
    # 通用参数
    name=Form(..., min_length=3, max_length=50, description="项目名称"),
    description=Form(None, max_length=500, description="项目描述"),
    type=Form(..., description="项目类型"),
    tags=Form(None, description="项目标签，逗号分隔或JSON数组"),
    dependencies=Form(None, description="Python依赖包JSON数组"),
    venv_scope=Form(..., description="虚拟环境作用域：shared/private，必须选择"),
    shared_venv_key=Form(None, description="共享环境标识（共享环境必填）"),
    interpreter_source=Form("mise", description="解释器来源：mise/local（私有环境时使用）"),
    python_version=Form(None, description="Python版本（私有环境必填）"),
    python_bin=Form(None, description="当来源为local时的python路径（私有环境时使用）"),

    # 文件项目参数
    entry_point=Form(None, max_length=255, description="入口文件路径"),
    runtime_config=Form(None, description="运行时配置JSON"),
    environment_vars=Form(None, description="环境变量JSON"),

    # 规则项目参数
    engine=Form("requests", description="采集引擎 (browser/requests/curl_cffi)"),
    target_url=Form(None, max_length=2000, description="目标URL"),
    url_pattern=Form(None, max_length=500, description="URL匹配模式"),
    request_method=Form("GET", description="请求方法 (GET/POST/PUT/DELETE)"),
    callback_type=Form("list", description="回调类型 (list/detail)"),
    extraction_rules=Form(None, description="提取规则数组JSON"),
    pagination_config=Form(None, description="分页配置JSON"),
    max_pages=Form(10, ge=1, le=1000, description="最大页数"),
    start_page: Optional[int] = Form(1, ge=1, description="起始页码"),
    request_delay: Optional[int] = Form(1000, ge=0, description="请求间隔(ms)"),
    priority: Optional[int] = Form(0, description="优先级"),
    headers: Optional[str] = Form(None, description="请求头JSON"),
    cookies: Optional[str] = Form(None, description="Cookie JSON"),

    # 代码项目参数
    language: Optional[str] = Form("python", max_length=50, description="编程语言"),
    version: Optional[str] = Form("1.0.0", max_length=20, description="版本号"),
    code_entry_point: Optional[str] = Form(None, max_length=255, description="入口函数"),
    documentation: Optional[str] = Form(None, description="代码文档"),
    code_content: Optional[str] = Form(None, description="代码内容（直接提交代码时使用）"),
):
    """获取项目创建Form参数的依赖函数"""
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
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(20, ge=1, le=100, description="每页数量"),
    type: Optional[ProjectType] = Query(None, description="项目类型筛选"),
    status: Optional[ProjectStatus] = Query(None, description="项目状态筛选"),
    tag: Optional[str] = Query(None, description="标签筛选"),
    created_by: Optional[int] = Query(None, description="创建者ID筛选"),
):
    """获取项目列表查询参数的依赖函数"""
    return ProjectListQueryRequest(
        page=page,
        size=size,
        type=type,
        status=status,
        tag=tag,
        created_by=created_by,
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
    form_data: ProjectCreateFormRequest = Depends(get_project_create_form),
    file: Optional[UploadFile] = File(None, description="项目文件（文件项目必需）"),
    files: Optional[List[UploadFile]] = File(None, description="多个项目文件（可选）"),
    code_file: Optional[UploadFile] = File(None, description="代码文件（代码项目必需）"),
    current_user_id: int = Depends(get_current_user_id)
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

    return success_response(response_data, message=Messages.CREATED_SUCCESS, code=201)


@project_router.get(
    "",
    response_model=PaginationResponse[ProjectListResponse],
    summary="获取项目列表",
    description="获取当前用户的项目列表，支持分页和筛选",
    response_description="返回项目列表和分页信息"
)
@project_router.get(
    "/",
    response_model=PaginationResponse[ProjectListResponse],
    summary="获取项目列表", 
    description="获取当前用户的项目列表，支持分页和筛选",
    response_description="返回项目列表和分页信息"
)
@fast_response(cache_ttl=120, namespace="project:list")  # 列表缓存2分钟
@monitor_performance(slow_threshold=0.5)  # 监控超过500ms的查询
@optimize_large_response(chunk_size=50)  # 大响应优化
async def get_projects_list(
    query_params: ProjectListQueryRequest = Depends(get_project_list_query),
    current_user_id: int = Depends(get_current_user_id),
    current_user = Depends(get_current_user)
):
    # 获取当前用户信息以判断权限
    from src.services.users.user_service import user_service
    user = await user_service.get_user_by_id(current_user.user_id)
    
    # 管理员可以查看所有项目，普通用户只能查看自己创建的项目
    user_filter = None if user and user.is_admin else current_user_id
    
    # 如果是管理员且指定了created_by筛选，使用created_by作为筛选条件
    if user and user.is_admin and query_params.created_by is not None:
        user_filter = query_params.created_by

    # 查询项目列表
    projects, total = await project_service.get_projects_list(
        page=query_params.page,
        size=query_params.size,
        project_type=query_params.type,
        status=query_params.status.value if query_params.status else None,
        tag=query_params.tag,
        user_id=user_filter
    )

    # 转换为响应格式
    project_list = []
    for project in projects:
        project_data = ProjectListResponse(
            id=project.id,
            name=project.name,
            description=project.description,
            type=project.type,
            status=project.status,
            tags=project.tags,
            created_at=project.created_at,
            created_by=project.user_id,  # 映射 user_id 到 created_by
            created_by_username=getattr(project, 'created_by_username', None),
            download_count=project.download_count,
            star_count=project.star_count
        )
        project_list.append(project_data)

    return page_response(
        items=project_list,
        total=total,
        page=query_params.page,
        size=query_params.size,
        message=Messages.QUERY_SUCCESS
    )


@project_router.get(
    "/{project_id}",
    response_model=BaseResponse[ProjectResponse],
    summary="获取项目详情",
    description="根据项目ID获取项目详细信息",
    response_description="返回项目详细信息"
)
@fast_response(cache_ttl=120, namespace="project:detail", key_prefix_fn=lambda args, kwargs: str(kwargs.get('project_id') if 'project_id' in kwargs else (args[0] if args else '')))
async def get_project_detail(
    project_id: int,
    current_user_id: int = Depends(get_current_user_id)
):
    
    

    # 查询项目
    project = await project_service.get_project_by_id(project_id, current_user_id)
    if not project:
        raise ProjectNotFoundException(project_id)

    # 构建响应数据
    response_data = create_project_response(project)

    # 根据项目类型添加详细信息 - 使用应用层关联
    from src.services.projects.relation_service import relation_service
    if project.type == ProjectType.FILE:
        file_detail = await relation_service.get_project_file_detail(project.id)
        if file_detail:
            response_data.file_info = {
                "original_name": file_detail.original_name,
                "file_size": file_detail.file_size,
                "file_hash": file_detail.file_hash,
                "file_path": file_detail.file_path,
                "file_type": file_detail.file_type,
                "entry_point": file_detail.entry_point,
                "runtime_config": file_detail.runtime_config,
                "environment_vars": file_detail.environment_vars
            }
    elif project.type == ProjectType.RULE:
        rule_detail = await relation_service.get_project_rule_detail(project.id)
        if rule_detail:
            response_data.rule_info = {
                "engine": rule_detail.engine,
                "target_url": rule_detail.target_url,
                "url_pattern": rule_detail.url_pattern,
                "callback_type": rule_detail.callback_type,
                "request_method": rule_detail.request_method,
                "extraction_rules": rule_detail.extraction_rules,
                "data_schema": rule_detail.data_schema,
                "pagination_config": rule_detail.pagination_config,
                "max_pages": rule_detail.max_pages,
                "start_page": rule_detail.start_page,
                "request_delay": rule_detail.request_delay,
                "retry_count": rule_detail.retry_count,
                "timeout": rule_detail.timeout,
                "priority": getattr(rule_detail, 'priority', 0),
                "dont_filter": getattr(rule_detail, 'dont_filter', False),
                "headers": rule_detail.headers,
                "cookies": rule_detail.cookies,
                "proxy_config": rule_detail.proxy_config,
                "task_config": getattr(rule_detail, 'task_config', None)
            }
    elif project.type == ProjectType.CODE:
        code_detail = await relation_service.get_project_code_detail(project.id)
        if code_detail:
            response_data.code_info = {
                "content": code_detail.content,
                "language": code_detail.language,
                "version": code_detail.version,
                "content_hash": code_detail.content_hash,
                "entry_point": code_detail.entry_point,
                "runtime_config": code_detail.runtime_config,
                "environment_vars": code_detail.environment_vars
            }

    return success_response(response_data, message=Messages.QUERY_SUCCESS)



@project_router.put(
    "/{project_id}",
    response_model=BaseResponse[ProjectResponse],
    summary="更新项目（统一API）",
    description="统一更新项目的所有信息，支持基本信息和各类型项目的详细配置一次性更新",
    response_description="返回更新后的项目信息"
)
async def update_project(
    project_id: int,
    request: UnifiedProjectUpdateRequest,
    current_user_id: int = Depends(get_current_user_id)
):
    """统一更新项目 - 支持所有项目类型的字段更新"""

    # 使用统一服务更新项目
    project = await unified_project_service.update_project_unified(
        project_id, request, current_user_id
    )
    if not project:
        raise ProjectNotFoundException(project_id)

    # 构建响应数据 - 包含详细信息
    response_data = ProjectResponse.from_orm(project)
    
    # 根据项目类型添加详细信息
    from src.services.projects.relation_service import relation_service
    if project.type == ProjectType.FILE:
        file_detail = await relation_service.get_project_file_detail(project.id)
        if file_detail:
            response_data.file_info = {
                "original_name": file_detail.original_name,
                "file_size": file_detail.file_size,
                "file_hash": file_detail.file_hash,
                "file_path": file_detail.file_path,
                "file_type": file_detail.file_type,
                "entry_point": file_detail.entry_point,
                "runtime_config": file_detail.runtime_config,
                "environment_vars": file_detail.environment_vars
            }
    elif project.type == ProjectType.RULE:
        rule_detail = await relation_service.get_project_rule_detail(project.id)
        if rule_detail:
            response_data.rule_info = {
                "engine": rule_detail.engine,
                "target_url": rule_detail.target_url,
                "url_pattern": rule_detail.url_pattern,
                "callback_type": rule_detail.callback_type,
                "request_method": rule_detail.request_method,
                "extraction_rules": rule_detail.extraction_rules,
                "data_schema": rule_detail.data_schema,
                "pagination_config": rule_detail.pagination_config,
                "max_pages": rule_detail.max_pages,
                "start_page": rule_detail.start_page,
                "request_delay": rule_detail.request_delay,
                "retry_count": rule_detail.retry_count,
                "timeout": rule_detail.timeout,
                "priority": getattr(rule_detail, 'priority', 0),
                "dont_filter": getattr(rule_detail, 'dont_filter', False),
                "headers": rule_detail.headers,
                "cookies": rule_detail.cookies,
                "proxy_config": rule_detail.proxy_config,
                "task_config": getattr(rule_detail, 'task_config', None)
            }
    elif project.type == ProjectType.CODE:
        code_detail = await relation_service.get_project_code_detail(project.id)
        if code_detail:
            response_data.code_info = {
                "content": code_detail.content,
                "language": code_detail.language,
                "version": code_detail.version,
                "content_hash": code_detail.content_hash,
                "entry_point": code_detail.entry_point,
                "runtime_config": code_detail.runtime_config,
                "environment_vars": code_detail.environment_vars
            }

    return success_response(response_data, message=Messages.UPDATED_SUCCESS)


@project_router.delete(
    "/{project_id}",
    response_model=BaseResponse[None],
    summary="删除项目",
    description="删除指定的项目（不可逆操作）",
    response_description="返回删除操作结果"
)
async def delete_project(
    project_id: int,
    current_user_id: int = Depends(get_current_user_id)
):

    # 删除项目
    deleted = await project_service.delete_project(project_id, current_user_id)
    if not deleted:
        raise ProjectNotFoundException(project_id)

    return success_response(None, message=Messages.DELETED_SUCCESS)


@project_router.post(
    "/batch-delete",
    response_model=BaseResponse[Dict[str, Any]],
    summary="批量删除项目",
    description="批量删除多个项目（不可逆操作）",
    response_description="返回批量删除操作结果"
)
@fast_response(background_execution=True)  # 后台执行
@monitor_performance(slow_threshold=2.0)  # 监控超过2秒的批量操作
async def batch_delete_projects(
    request: Dict[str, List[int]] = Body(..., description="包含项目ID列表的请求体"),
    current_user_id: int = Depends(get_current_user_id)
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
    project_id: int,
    current_user_id: int = Depends(get_current_user_id)
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
    project_id: int,
    request: ProjectRuleUpdateRequest,
    current_user_id: int = Depends(get_current_user_id)
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
    project_id: int,
    request: ProjectCodeUpdateRequest,
    current_user_id: int = Depends(get_current_user_id)
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
    project_id: int,
    # 表单数据
    entry_point: Optional[str] = Form(None, description="入口文件路径"),
    runtime_config: Optional[str] = Form(None, description="运行时配置JSON"),
    environment_vars: Optional[str] = Form(None, description="环境变量JSON"),
    # 文件上传
    file: Optional[UploadFile] = File(None, description="新的项目文件（可选）"),
    current_user_id: int = Depends(get_current_user_id)
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
        parsed_runtime_config = None
        if runtime_config:
            try:
                import ujson
                parsed_runtime_config = ujson.loads(runtime_config)
            except:
                parsed_runtime_config = None

        parsed_environment_vars = None
        if environment_vars:
            try:
                import ujson
                parsed_environment_vars = ujson.loads(environment_vars)
            except:
                parsed_environment_vars = None

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
    project_id: int,
    current_user_id: int = Depends(get_current_user_id)
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
            project_id=project.id,
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
    project_id: int,
    file_path: str = Query(..., description="文件相对路径"),
    current_user_id: int = Depends(get_current_user_id)
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


@project_router.get(
    "/{project_id}/files/download",
    summary="下载项目文件",
    description="下载项目的原始文件或解压后的特定文件",
    response_description="返回文件下载"
)
async def download_project_file(
    project_id: int,
    file_path: Optional[str] = Query(None, description="文件相对路径（可选，不提供则下载原始文件）"),
    current_user_id: int = Depends(get_current_user_id)
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
