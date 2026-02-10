"""审计日志 API"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger

from antcode_web_api.response import success
from antcode_core.common.security.auth import TokenData, get_current_super_admin, get_current_user
from antcode_core.domain.models import User
from antcode_core.application.services.audit import audit_service

router = APIRouter()


@router.get("/logs", summary="获取审计日志", description="获取审计日志列表（仅管理员）")
async def get_audit_logs(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(50, ge=1, le=200, description="每页数量"),
    action: str | None = Query(None, description="操作类型"),
    resource_type: str | None = Query(None, description="资源类型"),
    username: str | None = Query(None, description="用户名"),
    user_id: int | None = Query(None, ge=1, description="用户ID"),
    start_date: str | None = Query(None, description="开始日期"),
    end_date: str | None = Query(None, description="结束日期"),
    success_filter: bool | None = Query(None, alias="success", description="成功状态"),
    current_user: TokenData = Depends(get_current_user),
):
    """获取审计日志"""
    # 检查管理员权限
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")

    # 解析日期
    start_dt = None
    end_dt = None
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="开始日期格式错误")
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        except ValueError:
            raise HTTPException(status_code=400, detail="结束日期格式错误")

    result = await audit_service.get_logs(
        page=page,
        page_size=page_size,
        action=action,
        resource_type=resource_type,
        username=username,
        user_id=user_id,
        start_date=start_dt,
        end_date=end_dt,
        success=success_filter,
    )

    return success(result)


@router.get("/stats", summary="获取审计统计", description="获取审计日志统计信息（仅管理员）")
async def get_audit_stats(
    days: int = Query(7, ge=1, le=90, description="统计天数"),
    current_user: TokenData = Depends(get_current_user),
):
    """获取审计统计"""
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")

    stats = await audit_service.get_stats(days=days)
    return success(stats)


@router.get(
    "/user/{username}",
    summary="获取用户活动",
    description="获取指定用户的活动记录（仅管理员）",
)
async def get_user_activity(
    username: str,
    days: int = Query(30, ge=1, le=90, description="查询天数"),
    limit: int = Query(100, ge=1, le=500, description="返回数量"),
    current_user: TokenData = Depends(get_current_user),
):
    """获取用户活动"""
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")

    activity = await audit_service.get_user_activity(username=username, days=days, limit=limit)

    return success({"username": username, "activity": activity})


@router.delete(
    "/cleanup",
    summary="清理旧日志",
    description="清理指定天数之前的审计日志（仅超级管理员）",
)
async def cleanup_audit_logs(
    days: int = Query(90, ge=30, le=365, description="保留天数"),
    current_user: TokenData = Depends(get_current_super_admin),
):
    """清理旧日志"""
    deleted = await audit_service.cleanup_old_logs(days=days)

    logger.info(f"审计日志清理: {deleted} 条记录被删除 by {current_user.username}")

    return success({"deleted": deleted}, message=f"已清理 {deleted} 条旧日志")


@router.get("/actions", summary="获取操作类型列表", description="获取所有可用的审计操作类型")
async def get_audit_actions(current_user: TokenData = Depends(get_current_user)):
    """获取操作类型列表"""
    from antcode_core.domain.models.audit_log import AuditAction

    actions = [
        {"value": action.value, "label": _get_action_label(action)} for action in AuditAction
    ]

    return success(actions)


def _get_action_label(action) -> str:
    """获取操作类型的中文标签"""
    labels = {
        "login": "用户登录",
        "logout": "用户登出",
        "login_failed": "登录失败",
        "password_change": "修改密码",
        "user_create": "创建用户",
        "user_update": "更新用户",
        "user_delete": "删除用户",
        "user_role_change": "修改角色",
        "project_create": "创建项目",
        "project_update": "更新项目",
        "project_delete": "删除项目",
        "task_create": "创建任务",
        "task_update": "更新任务",
        "task_delete": "删除任务",
        "task_execute": "执行任务",
        "task_stop": "停止任务",
        "worker_create": "创建 Worker",
        "worker_update": "更新 Worker",
        "worker_delete": "删除 Worker",
        "worker_resource_update": "更新 Worker 资源",
        "config_update": "更新配置",
        "alert_config_update": "更新告警配置",
        "env_create": "创建运行时",
        "env_delete": "删除运行时",
        "export_data": "导出数据",
        "import_data": "导入数据",
    }
    return labels.get(action.value, action.value)
