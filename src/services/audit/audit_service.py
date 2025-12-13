"""审计日志服务"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from loguru import logger

from src.models.audit_log import AuditLog, AuditAction


class AuditService:
    """审计日志服务"""

    async def log(
        self,
        action: AuditAction,
        resource_type: str,
        username: str,
        resource_id: Optional[str] = None,
        resource_name: Optional[str] = None,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        description: Optional[str] = None,
        old_value: Optional[Dict] = None,
        new_value: Optional[Dict] = None,
        success: bool = True,
        error_message: Optional[str] = None
    ) -> AuditLog:
        """
        记录审计日志
        
        Args:
            action: 操作类型
            resource_type: 资源类型 (user, project, task, node, config等)
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
                error_message=error_message
            )

            # 记录到日志文件
            log_msg = f"[审计] {action.value} | {username} | {resource_type}"
            if resource_name:
                log_msg += f" | {resource_name}"
            if not success:
                log_msg += f" | 失败: {error_message}"

            logger.info(log_msg)

            return audit_log

        except Exception as e:
            logger.error(f"记录审计日志失败: {e}")
            raise

    async def log_login(
        self,
        username: str,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None
    ) -> AuditLog:
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
            error_message=error_message
        )

    async def log_logout(
        self,
        username: str,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None
    ) -> AuditLog:
        """记录登出日志"""
        return await self.log(
            action=AuditAction.LOGOUT,
            resource_type="auth",
            username=username,
            user_id=user_id,
            ip_address=ip_address,
            description="用户登出"
        )

    async def log_user_action(
        self,
        action: AuditAction,
        operator_username: str,
        target_user_id: int,
        target_username: str,
        operator_id: Optional[int] = None,
        ip_address: Optional[str] = None,
        old_value: Optional[Dict] = None,
        new_value: Optional[Dict] = None,
        description: Optional[str] = None
    ) -> AuditLog:
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
            description=description
        )

    async def log_project_action(
        self,
        action: AuditAction,
        username: str,
        project_id: int,
        project_name: str,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
        old_value: Optional[Dict] = None,
        new_value: Optional[Dict] = None,
        description: Optional[str] = None
    ) -> AuditLog:
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
            description=description
        )

    async def log_task_action(
        self,
        action: AuditAction,
        username: str,
        task_id: int,
        task_name: str,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
        description: Optional[str] = None
    ) -> AuditLog:
        """记录任务操作"""
        return await self.log(
            action=action,
            resource_type="task",
            resource_id=str(task_id),
            resource_name=task_name,
            username=username,
            user_id=user_id,
            ip_address=ip_address,
            description=description
        )

    async def log_config_change(
        self,
        username: str,
        config_key: str,
        old_value: Any,
        new_value: Any,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None
    ) -> AuditLog:
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
            description=f"修改配置: {config_key}"
        )

    # ========== 查询方法 ==========

    async def get_logs(
        self,
        page: int = 1,
        page_size: int = 50,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        username: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        success: Optional[bool] = None
    ) -> Dict[str, Any]:
        """
        查询审计日志
        
        Args:
            page: 页码
            page_size: 每页数量
            action: 操作类型过滤
            resource_type: 资源类型过滤
            username: 用户名过滤
            start_date: 开始时间
            end_date: 结束时间
            success: 成功状态过滤
        
        Returns:
            分页的审计日志列表
        """
        query = AuditLog.all()

        if action:
            query = query.filter(action=action)
        if resource_type:
            query = query.filter(resource_type=resource_type)
        if username:
            query = query.filter(username__icontains=username)
        if start_date:
            query = query.filter(created_at__gte=start_date)
        if end_date:
            query = query.filter(created_at__lte=end_date)
        if success is not None:
            query = query.filter(success=success)

        total = await query.count()
        logs = await query.offset((page - 1) * page_size).limit(page_size)

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
                    "username": log.username,
                    "ip_address": log.ip_address,
                    "description": log.description,
                    "success": log.success,
                    "error_message": log.error_message,
                    "created_at": log.created_at.isoformat()
                }
                for log in logs
            ]
        }

    async def get_user_activity(
        self,
        username: str,
        days: int = 30,
        limit: int = 100
    ) -> List[Dict]:
        """获取用户活动记录"""
        start_date = datetime.now() - timedelta(days=days)

        logs = await AuditLog.filter(
            username=username,
            created_at__gte=start_date
        ).order_by("-created_at").limit(limit)

        return [
            {
                "action": log.action.value,
                "resource_type": log.resource_type,
                "resource_name": log.resource_name,
                "description": log.description,
                "success": log.success,
                "created_at": log.created_at.isoformat()
            }
            for log in logs
        ]

    async def get_stats(
        self,
        days: int = 7
    ) -> Dict[str, Any]:
        """获取审计统计"""
        start_date = datetime.now() - timedelta(days=days)

        # 总数
        total = await AuditLog.filter(created_at__gte=start_date).count()

        # 按操作类型统计
        all_logs = await AuditLog.filter(created_at__gte=start_date).all()

        by_action = {}
        by_user = {}
        by_resource = {}
        failed_count = 0

        for log in all_logs:
            # 按操作统计
            action_key = log.action.value
            by_action[action_key] = by_action.get(action_key, 0) + 1

            # 按用户统计
            by_user[log.username] = by_user.get(log.username, 0) + 1

            # 按资源类型统计
            by_resource[log.resource_type] = by_resource.get(log.resource_type, 0) + 1

            # 失败统计
            if not log.success:
                failed_count += 1

        return {
            "total": total,
            "failed_count": failed_count,
            "success_rate": round((total - failed_count) / total * 100, 2) if total > 0 else 100,
            "by_action": by_action,
            "by_user": dict(sorted(by_user.items(), key=lambda x: x[1], reverse=True)[:10]),
            "by_resource": by_resource,
            "days": days
        }

    async def cleanup_old_logs(self, days: int = 90) -> int:
        """清理旧日志"""
        cutoff_date = datetime.now() - timedelta(days=days)
        deleted = await AuditLog.filter(created_at__lt=cutoff_date).delete()
        logger.info(f"清理了 {deleted} 条超过 {days} 天的审计日志")
        return deleted


# 全局实例
audit_service = AuditService()
