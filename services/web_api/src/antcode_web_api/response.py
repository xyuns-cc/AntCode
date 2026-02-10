"""统一响应工具（Web API）"""

from enum import IntEnum

from antcode_core.domain.schemas.common import BaseResponse, PaginationInfo, PaginationResponse


class ResponseCode(IntEnum):
    """HTTP 响应状态码"""

    # 成功
    SUCCESS = 200
    CREATED = 201
    ACCEPTED = 202
    # 客户端错误
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    CONFLICT = 409
    UNPROCESSABLE = 422
    TOO_MANY_REQUESTS = 429
    # 服务端错误
    SERVER_ERROR = 500
    BAD_GATEWAY = 502
    SERVICE_UNAVAILABLE = 503


class Messages:
    """标准响应消息"""

    # 成功消息
    OPERATION_SUCCESS = "操作成功"
    CREATED_SUCCESS = "创建成功"
    UPDATED_SUCCESS = "更新成功"
    DELETED_SUCCESS = "删除成功"
    QUERY_SUCCESS = "查询成功"
    LOGIN_SUCCESS = "登录成功"
    LOGOUT_SUCCESS = "退出成功"
    # 错误消息
    UNAUTHORIZED = "未授权或会话已过期"
    FORBIDDEN = "权限不足"
    BAD_REQUEST = "请求参数无效"
    NOT_FOUND = "资源不存在"
    CONFLICT = "资源冲突"
    SERVER_ERROR = "服务器内部错误"


def success(data=None, message=Messages.OPERATION_SUCCESS, code=ResponseCode.SUCCESS):
    """构建成功响应"""
    return BaseResponse(success=True, code=int(code), message=message, data=data)


def error(message, code, data=None):
    """构建错误响应"""
    return BaseResponse(success=False, code=int(code), message=message, data=data)


def page(items, total, page, size, message=Messages.QUERY_SUCCESS, code=ResponseCode.SUCCESS):
    """构建分页响应"""
    pages = (total + size - 1) // size if size > 0 else 0
    return PaginationResponse(
        success=True,
        code=int(code),
        message=message,
        data=list(items),
        pagination=PaginationInfo(page=page, size=size, total=total, pages=pages),
    )


def task_list(total, page_num, size, items):
    """构建任务列表响应"""
    from antcode_core.domain.schemas.task import TaskListResponse

    return TaskListResponse(total=total, page=page_num, size=size, items=list(items))


def execution_list(total, page_num, size, items):
    """构建执行记录列表响应"""
    from antcode_core.domain.schemas.task import TaskRunListResponse

    return TaskRunListResponse(total=total, page=page_num, size=size, items=list(items))


# ============================================================================
# 领域响应构建器
# ============================================================================


class ProjectResponseBuilder:
    """项目响应构建器"""

    @staticmethod
    def build_detail(project):
        """构建项目详情响应"""
        from antcode_core.domain.schemas.project import ProjectResponse

        created_by_public_id = getattr(project, "created_by_public_id", None)
        if not created_by_public_id:
            created_by_public_id = str(project.user_id) if project.user_id else None

        # 获取执行策略相关字段
        execution_strategy = getattr(project, "execution_strategy", None)
        if execution_strategy and hasattr(execution_strategy, "value"):
            execution_strategy = execution_strategy.value

        bound_worker_id = getattr(project, "bound_worker_id", None)
        bound_worker_name = getattr(project, "bound_worker_name", None)
        fallback_enabled = getattr(project, "fallback_enabled", None)

        return ProjectResponse.model_construct(
            id=project.public_id,
            name=project.name,
            description=project.description or "",
            type=project.type,
            status=project.status,
            tags=project.tags or [],
            dependencies=project.dependencies,
            created_at=project.created_at,
            updated_at=project.updated_at,
            created_by=created_by_public_id,
            created_by_username=getattr(project, "created_by_username", None),
            download_count=project.download_count,
            star_count=project.star_count,
            file_info=getattr(project, "file_info", None),
            rule_info=getattr(project, "rule_info", None),
            code_info=getattr(project, "code_info", None),
            python_version=getattr(project, "python_version", None),
            runtime_scope=getattr(project, "runtime_scope", None),
            runtime_kind=getattr(project, "runtime_kind", None),
            runtime_locator=getattr(project, "runtime_locator", None),
            # 执行策略字段
            execution_strategy=execution_strategy,
            bound_worker_id=str(bound_worker_id) if bound_worker_id else None,
            bound_worker_name=bound_worker_name,
            fallback_enabled=fallback_enabled,
        )

    @staticmethod
    def build_list_item(project):
        """构建项目列表项响应"""
        from antcode_core.domain.schemas.project import ProjectListResponse

        created_by_public_id = getattr(project, "created_by_public_id", None)
        if not created_by_public_id and project.user_id:
            created_by_public_id = str(project.user_id)

        return ProjectListResponse(
            id=project.public_id,
            name=project.name,
            description=project.description or "",
            type=project.type,
            status=project.status,
            tags=project.tags or [],
            created_at=project.created_at,
            created_by=created_by_public_id,
            created_by_username=getattr(project, "created_by_username", None),
            download_count=project.download_count,
            star_count=project.star_count,
            task_count=getattr(project, "task_count", 0),
        )

    @staticmethod
    def build_list(projects):
        """批量构建项目列表响应"""
        return [ProjectResponseBuilder.build_list_item(p) for p in projects]


class TaskResponseBuilder:
    """任务响应构建器"""

    @staticmethod
    def build_detail(task):
        """构建任务详情响应"""
        from antcode_core.domain.schemas.task import TaskResponse

        project_public_id = getattr(task, "project_public_id", None) or str(task.project_id)
        created_by_public_id = getattr(task, "created_by_public_id", None) or str(task.user_id)

        # 获取执行策略相关字段
        execution_strategy = getattr(task, "execution_strategy", None)
        if execution_strategy and hasattr(execution_strategy, "value"):
            execution_strategy = execution_strategy.value

        specified_worker_id = getattr(task, "specified_worker_public_id", None)
        specified_worker_name = getattr(task, "specified_worker_name", None)

        # 项目执行配置
        project_execution_strategy = getattr(task, "project_execution_strategy", None)
        if project_execution_strategy and hasattr(project_execution_strategy, "value"):
            project_execution_strategy = project_execution_strategy.value
        project_bound_worker_id = getattr(task, "project_bound_worker_public_id", None)
        project_bound_worker_name = getattr(task, "project_bound_worker_name", None)

        return TaskResponse.model_construct(
            id=task.public_id,
            name=task.name,
            description=task.description,
            project_id=project_public_id,
            schedule_type=task.schedule_type,
            is_active=task.is_active,
            task_type=task.task_type,
            status=task.status,
            cron_expression=task.cron_expression,
            interval_seconds=task.interval_seconds,
            scheduled_time=task.scheduled_time,
            last_run_time=task.last_run_time,
            next_run_time=task.next_run_time,
            created_at=task.created_at,
            updated_at=task.updated_at,
            created_by=created_by_public_id,
            created_by_username=getattr(task, "created_by_username", None),
            # 执行策略字段
            execution_strategy=execution_strategy,
            specified_worker_id=str(specified_worker_id) if specified_worker_id else None,
            specified_worker_name=specified_worker_name,
            # 项目执行配置
            project_execution_strategy=project_execution_strategy,
            project_bound_worker_id=str(project_bound_worker_id)
            if project_bound_worker_id
            else None,
            project_bound_worker_name=project_bound_worker_name,
            fallback_enabled=getattr(task, "fallback_enabled", None),
            # 运行时
            runtime_scope=getattr(task, "runtime_scope", None),
            runtime_kind=getattr(task, "runtime_kind", None),
            python_version=getattr(task, "python_version", None),
            runtime_locator=getattr(task, "runtime_locator", None),
        )

    @staticmethod
    def build_list(tasks):
        """批量构建任务列表响应"""
        return [TaskResponseBuilder.build_detail(t) for t in tasks]


class ExecutionResponseBuilder:
    """执行记录响应构建器"""

    @staticmethod
    def build_detail(execution):
        """构建执行记录详情响应"""
        from antcode_core.domain.schemas.task import TaskRunResponse

        return TaskRunResponse.from_orm(execution)

    @staticmethod
    def build_list(executions):
        """批量构建执行记录列表响应"""
        from antcode_core.domain.schemas.task import TaskRunResponse

        return [TaskRunResponse.from_orm(e) for e in executions]
