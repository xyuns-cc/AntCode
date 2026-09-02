"""审计日志服务"""

import asyncio
from datetime import datetime, timedelta

from loguru import logger

from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.domain.models.audit_log import AuditAction, AuditLog


class AuditService:
    """审计日志服务"""

    async def log(
        self,
        action,
        resource_type,
        username,
        resource_id=None,
        resource_name=None,
        user_id=None,
        ip_address=None,
        user_agent=None,
        description=None,
        old_value=None,
        new_value=None,
        success=True,
        error_message=None,
    ):
        """
        记录审计日志

        Args:
            action: 操作类型
            resource_type: 资源类型 (user, project, task, worker, config等)
            username: 操作用户名
            resource_id: 资源ID
            resource_name: 资源名称
            user_id: 用户ID
            ip_address: IP地址
            user_agent: User-Agent
            description: 操作描述
            old_value: 修改前的值
            new_value: 修改后的值
            success: 是否成功
            error_message: 错误信息

        Returns:
            创建的审计日志记录
        """
        try:
            normalized_error = normalize_persisted_error_message(error_message)
            audit_log = await AuditLog.create(
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id) if resource_id else None,
                resource_name=resource_name,
                user_id=user_id,
                username=username,
                ip_address=ip_address,
                user_agent=user_agent,
                description=description,
                old_value=old_value,
                new_value=new_value,
                success=success,
                error_message=normalized_error,
            )

            # 记录到日志文件
            log_msg = f"[审计] {action.value} | {username} | {resource_type}"
            if resource_name:
                log_msg += f" | {resource_name}"
            if not success:
                log_msg += f" | 失败: {normalized_error}"

            logger.info(log_msg)

            return audit_log

        except Exception as e:
            logger.error(f"记录审计日志失败: {e}")
            raise

    async def log_login(
        self,
        username,
        user_id=None,
        ip_address=None,
        user_agent=None,
        success=True,
        error_message=None,
    ):
        """记录登录日志"""
        action = AuditAction.LOGIN if success else AuditAction.LOGIN_FAILED
        return await self.log(
            action=action,
            resource_type="auth",
            username=username,
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            description=f"用户{'登录成功' if success else '登录失败'}",
            success=success,
            error_message=error_message,
        )

    async def log_logout(self, username, user_id=None, ip_address=None):
        """记录登出日志"""
        return await self.log(
            action=AuditAction.LOGOUT,
            resource_type="auth",
            username=username,
            user_id=user_id,
            ip_address=ip_address,
            description="用户登出",
        )

    async def log_user_action(
        self,
        action,
        operator_username,
        target_user_id,
        target_username,
        operator_id=None,
        ip_address=None,
        old_value=None,
        new_value=None,
        description=None,
    ):
        """记录用户管理操作"""
        return await self.log(
            action=action,
            resource_type="user",
            resource_id=str(target_user_id),
            resource_name=target_username,
            username=operator_username,
            user_id=operator_id,
            ip_address=ip_address,
            old_value=old_value,
            new_value=new_value,
            description=description,
        )

    async def log_project_action(
        self,
        action,
        username,
        project_id,
        project_name,
        user_id=None,
        ip_address=None,
        old_value=None,
        new_value=None,
        description=None,
    ):
        """记录项目操作"""
        return await self.log(
            action=action,
            resource_type="project",
            resource_id=str(project_id),
            resource_name=project_name,
            username=username,
            user_id=user_id,
            ip_address=ip_address,
            old_value=old_value,
            new_value=new_value,
            description=description,
        )

    async def log_task_action(
        self,
        action,
        username,
        task_id,
        task_name,
        user_id=None,
        ip_address=None,
        description=None,
    ):
        """记录任务操作"""
        return await self.log(
            action=action,
            resource_type="task",
            resource_id=str(task_id),
            resource_name=task_name,
            username=username,
            user_id=user_id,
            ip_address=ip_address,
            description=description,
        )

    async def log_config_change(self, username, config_key, old_value, new_value, user_id=None, ip_address=None):
        """记录配置变更"""
        return await self.log(
            action=AuditAction.CONFIG_UPDATE,
            resource_type="config",
            resource_id=config_key,
            resource_name=config_key,
            username=username,
            user_id=user_id,
            ip_address=ip_address,
            old_value={"value": str(old_value)} if old_value else None,
            new_value={"value": str(new_value)} if new_value else None,
            description=f"修改配置: {config_key}",
        )

    async def get_logs(
        self,
        page=1,
        page_size=50,
        action=None,
        resource_type=None,
        username=None,
        user_id=None,
        start_date=None,
        end_date=None,
        success=None,
    ):
        """查询审计日志（offset 分页）。

        分页保持 offset：调用方只有 ``GET /audit/logs``，它由 antd Pagination
        驱动，需要**精确** ``total`` 才能算页数——keyset 那套近似行数
        （``pg_class.reltuples``）会直接把页码算错。
        """
        query = AuditLog.all()

        if action:
            query = query.filter(action=action)
        if resource_type:
            query = query.filter(resource_type=resource_type)
        if username:
            query = query.filter(username__icontains=username)
        if user_id is not None:
            query = query.filter(user_id=user_id)
        if start_date:
            query = query.filter(created_at__gte=start_date)
        if end_date:
            query = query.filter(created_at__lte=end_date)
        if success is not None:
            query = query.filter(success=success)

        total = await query.count()
        logs = await query.order_by("-created_at", "-id").offset((page - 1) * page_size).limit(page_size)

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "action": log.action.value,
                    "resource_type": log.resource_type,
                    "resource_id": log.resource_id,
                    "resource_name": log.resource_name,
                    "user_id": log.user_id,
                    "username": log.username,
                    "ip_address": log.ip_address,
                    "description": log.description,
                    "old_value": log.old_value,
                    "new_value": log.new_value,
                    "success": log.success,
                    "error_message": log.error_message,
                    "created_at": log.created_at.isoformat(),
                }
                for log in logs
            ],
        }

    async def get_user_activity(self, username, days=30, limit=100):
        """获取用户活动记录"""
        start_date = datetime.now() - timedelta(days=days)

        logs = await AuditLog.filter(username=username, created_at__gte=start_date).order_by("-created_at").limit(limit)

        return [
            {
                "action": log.action.value,
                "resource_type": log.resource_type,
                "resource_name": log.resource_name,
                "description": log.description,
                "success": log.success,
                "created_at": log.created_at.isoformat(),
            }
            for log in logs
        ]

    async def get_stats(self, days=7):
        """获取审计统计

        使用数据库聚合查询优化性能，避免加载所有记录到内存。
        """
        from tortoise.functions import Count

        start_date = datetime.now() - timedelta(days=days)

        # 基础查询
        base_query = AuditLog.filter(created_at__gte=start_date)

        # 并行执行统计查询
        total, failed_count = await asyncio.gather(
            base_query.count(),
            base_query.filter(success=False).count(),
        )

        # 按操作类型统计 - 使用 values + annotate 进行分组统计
        action_stats = await base_query.annotate(count=Count("id")).group_by("action").values("action", "count")
        by_action = {item["action"]: item["count"] for item in action_stats}

        # 按用户统计（取 Top 10）
        user_stats = (
            await base_query.annotate(count=Count("id"))
            .group_by("username")
            .order_by("-count")
            .limit(10)
            .values("username", "count")
        )
        by_user = {item["username"]: item["count"] for item in user_stats}

        # 按资源类型统计
        resource_stats = (
            await base_query.annotate(count=Count("id")).group_by("resource_type").values("resource_type", "count")
        )
        by_resource = {item["resource_type"]: item["count"] for item in resource_stats}

        return {
            "total": total,
            "failed_count": failed_count,
            "success_rate": round((total - failed_count) / total * 100, 2) if total > 0 else 100,
            "by_action": by_action,
            "by_user": by_user,
            "by_resource": by_resource,
            "days": days,
        }

    async def cleanup_old_logs(self, days=90):
        """清理旧日志"""
        cutoff_date = datetime.now() - timedelta(days=days)
        deleted = await AuditLog.filter(created_at__lt=cutoff_date).delete()
        logger.info(f"清理了 {deleted} 条超过 {days} 天的审计日志")
        return deleted


audit_service = AuditService()
