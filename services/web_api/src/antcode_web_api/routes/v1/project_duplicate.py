"""Transactional project duplication helpers."""

from __future__ import annotations

from typing import Any

from antcode_core.application.services.projects.project_source_service import project_source_service
from antcode_core.domain.models.enums import ProjectType
from antcode_core.domain.models.project import Project, ProjectCode, ProjectFile, ProjectRule


async def duplicate_project_record(
    project: Project,
    name: str,
    *,
    user_id: int,
    connection: Any,
) -> Project:
    target = await _create_duplicate_project(project, name, user_id=user_id, connection=connection)
    await _duplicate_project_detail(project, target, connection=connection)
    await _ensure_duplicate_is_complete(target, connection)
    return target


async def _create_duplicate_project(
    project: Project,
    name: str,
    *,
    user_id: int,
    connection: Any,
) -> Project:
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
        using_db=connection,
    )


async def _duplicate_project_detail(source: Project, target: Project, *, connection: Any) -> None:
    if source.type == ProjectType.FILE:
        await _duplicate_file_project(source.id, target.id, connection)
        return
    if source.type == ProjectType.RULE:
        await _duplicate_rule_project(source.id, target.id, connection)
        return
    if source.type == ProjectType.CODE:
        await _duplicate_code_project(source.id, target.id, connection)


async def _duplicate_file_project(source_id: int, target_id: int, connection: Any) -> None:
    detail = await ProjectFile.filter(project_id=source_id).using_db(connection).first()
    if detail is None:
        raise ValueError("源文件项目配置不完整")
    await ProjectFile.create(
        project_id=target_id,
        language=detail.language,
        entry_point=detail.entry_point,
        runtime_config=detail.runtime_config,
        environment_vars=detail.environment_vars,
        using_db=connection,
    )
    await _copy_project_source(source_id, target_id, connection)


async def _duplicate_rule_project(source_id: int, target_id: int, connection: Any) -> None:
    detail = await ProjectRule.filter(project_id=source_id).using_db(connection).first()
    if detail is None:
        raise ValueError("源规则项目配置不完整")
    await ProjectRule.create(
        project_id=target_id,
        engine=detail.engine,
        region=detail.region,
        require_render=detail.require_render,
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
        resume_enabled=bool(getattr(detail, "resume_enabled", False)),
        dedup_config=getattr(detail, "dedup_config", None),
        using_db=connection,
    )


async def _duplicate_code_project(source_id: int, target_id: int, connection: Any) -> None:
    detail = await ProjectCode.filter(project_id=source_id).using_db(connection).first()
    if detail is None:
        raise ValueError("源代码项目配置不完整")
    await ProjectCode.create(
        project_id=target_id,
        language=detail.language,
        entry_point=detail.entry_point,
        runtime_config=detail.runtime_config,
        environment_vars=detail.environment_vars,
        documentation=detail.documentation,
        using_db=connection,
    )
    await _copy_project_source(source_id, target_id, connection)


async def _copy_project_source(source_id: int, target_id: int, connection: Any) -> None:
    await project_source_service.copy_source(
        source_project_id=source_id,
        target_project_id=target_id,
        connection=connection,
    )


async def _ensure_duplicate_is_complete(project: Project, connection: Any) -> None:
    models = {ProjectType.FILE: ProjectFile, ProjectType.RULE: ProjectRule, ProjectType.CODE: ProjectCode}
    if not await models[project.type].filter(project_id=project.id).using_db(connection).exists():
        raise ValueError("复制项目详情不完整")
    if project.type == ProjectType.RULE:
        return
    if await project_source_service.get_source(project.id, connection=connection) is None:
        raise ValueError("复制项目来源不完整")


__all__ = ["duplicate_project_record"]
