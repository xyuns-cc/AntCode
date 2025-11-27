"""应用层关联关系管理服务"""
from loguru import logger
from tortoise.exceptions import DoesNotExist

from src.models.project import Project, ProjectFile, ProjectRule, ProjectCode
from src.models.scheduler import ScheduledTask, TaskExecution
from src.models.user import User


class RelationService:
    """应用层关联关系管理器"""
    
    @staticmethod
    async def get_user_by_id(user_id):
        """根据ID获取用户"""
        try:
            return await User.get(id=user_id)
        except DoesNotExist:
            return None
    
    @staticmethod
    async def get_project_by_id(project_id):
        """根据ID获取项目"""
        try:
            return await Project.get(id=project_id)
        except DoesNotExist:
            return None
    
    @staticmethod
    async def get_task_by_id(task_id):
        """根据ID获取任务"""
        try:
            return await ScheduledTask.get(id=task_id)
        except DoesNotExist:
            return None
    
    @staticmethod
    async def get_execution_by_id(execution_id):
        """根据ID获取执行记录"""
        try:
            return await TaskExecution.get(id=execution_id)
        except DoesNotExist:
            return None
    
    # ==================== 项目关联关系 ====================
    
    @staticmethod
    async def get_project_with_user(project_id):
        """获取项目及其创建者信息"""
        project = await RelationService.get_project_by_id(project_id)
        if not project:
            return None
        
        user = await RelationService.get_user_by_id(project.user_id)
        
        return {
            "project": project,
            "user": user
        }
    
    @staticmethod
    async def get_user_projects(user_id):
        """获取用户的所有项目"""
        return await Project.filter(user_id=user_id).all()
    
    @staticmethod
    async def get_project_file_detail(project_id):
        """获取项目的文件详情"""
        try:
            return await ProjectFile.get(project_id=project_id)
        except DoesNotExist:
            return None
    
    @staticmethod
    async def get_project_rule_detail(project_id):
        """获取项目的规则详情"""
        try:
            return await ProjectRule.get(project_id=project_id)
        except DoesNotExist:
            return None
    
    @staticmethod
    async def get_project_code_detail(project_id):
        """获取项目的代码详情"""
        try:
            return await ProjectCode.get(project_id=project_id)
        except DoesNotExist:
            return None
    
    @staticmethod
    async def get_project_with_details(project_id):
        """获取项目及其所有详情"""
        project = await RelationService.get_project_by_id(project_id)
        if not project:
            return None
        
        user = await RelationService.get_user_by_id(project.user_id)
        
        # 根据项目类型获取对应的详情
        detail = None
        if project.type == "file":
            detail = await RelationService.get_project_file_detail(project_id)
        elif project.type == "rule":
            detail = await RelationService.get_project_rule_detail(project_id)
        elif project.type == "code":
            detail = await RelationService.get_project_code_detail(project_id)
        
        return {
            "project": project,
            "user": user,
            "detail": detail
        }
    
    # ==================== 任务关联关系 ====================
    
    @staticmethod
    async def get_task_with_project(task_id):
        """获取任务及其关联的项目信息"""
        task = await RelationService.get_task_by_id(task_id)
        if not task:
            return None
        
        project_info = await RelationService.get_project_with_details(task.project_id)
        user = await RelationService.get_user_by_id(task.user_id)
        
        return {
            "task": task,
            "project": project_info["project"] if project_info else None,
            "project_detail": project_info["detail"] if project_info else None,
            "user": user
        }
    
    @staticmethod
    async def get_project_tasks(project_id):
        """获取项目的所有任务"""
        return await ScheduledTask.filter(project_id=project_id).all()
    
    @staticmethod
    async def get_user_tasks(user_id):
        """获取用户的所有任务"""
        return await ScheduledTask.filter(user_id=user_id).all()
    
    # ==================== 执行记录关联关系 ====================
    
    @staticmethod
    async def get_execution_with_task(execution_id):
        """获取执行记录及其关联的任务信息"""
        execution = await RelationService.get_execution_by_id(execution_id)
        if not execution:
            return None
        
        task_info = await RelationService.get_task_with_project(execution.task_id)
        
        return {
            "execution": execution,
            "task": task_info["task"] if task_info else None,
            "project": task_info["project"] if task_info else None,
            "project_detail": task_info["project_detail"] if task_info else None
        }
    
    @staticmethod
    async def get_task_executions(task_id):
        """获取任务的所有执行记录"""
        return await TaskExecution.filter(task_id=task_id).order_by('-created_at').all()

    # ==================== 数据完整性检查 ====================

    @staticmethod
    async def validate_project_user(project_id, user_id):
        """验证项目是否属于指定用户"""
        project = await RelationService.get_project_by_id(project_id)
        return project is not None and project.user_id == user_id

    @staticmethod
    async def validate_task_user(task_id, user_id):
        """验证任务是否属于指定用户"""
        task = await RelationService.get_task_by_id(task_id)
        if not task:
            return False

        # 通过项目验证用户权限
        return await RelationService.validate_project_user(task.project_id, user_id)

    @staticmethod
    async def validate_execution_user(execution_id, user_id):
        """验证执行记录是否属于指定用户"""
        execution = await RelationService.get_execution_by_id(execution_id)
        if not execution:
            return False

        # 通过任务验证用户权限
        return await RelationService.validate_task_user(execution.task_id, user_id)

    # ==================== 级联删除操作 ====================

    @staticmethod
    async def delete_project_cascade(project_id):
        """级联删除项目及其相关数据"""
        deleted_counts = {
            "tasks": 0,
            "executions": 0,
            "details": 0
        }

        try:
            # 1. 获取项目的所有任务
            tasks = await ScheduledTask.filter(project_id=project_id).all()

            # 2. 删除每个任务的执行记录
            for task in tasks:
                deleted_counts["executions"] += await TaskExecution.filter(task_id=task.id).delete()

            # 3. 删除任务
            deleted_counts["tasks"] = await ScheduledTask.filter(project_id=project_id).delete()

            # 4. 删除项目详情（根据项目类型）
            # 尝试删除各种类型的项目详情
            file_deleted = await ProjectFile.filter(project_id=project_id).delete()
            rule_deleted = await ProjectRule.filter(project_id=project_id).delete()
            code_deleted = await ProjectCode.filter(project_id=project_id).delete()

            deleted_counts["details"] = file_deleted + rule_deleted + code_deleted

            # 5. 删除项目本身
            await Project.filter(id=project_id).delete()

            logger.info(f"级联删除项目 {project_id} 完成: {deleted_counts}")
            return deleted_counts

        except Exception as e:
            logger.error(f"级联删除项目 {project_id} 失败: {e}")
            raise

    @staticmethod
    async def delete_task_cascade(task_id):
        """级联删除任务及其相关数据"""
        deleted_counts = {
            "executions": 0
        }

        try:
            # 1. 删除执行记录
            deleted_counts["executions"] = await TaskExecution.filter(task_id=task_id).delete()

            # 2. 删除任务本身
            await ScheduledTask.filter(id=task_id).delete()

            logger.info(f"级联删除任务 {task_id} 完成: {deleted_counts}")
            return deleted_counts

        except Exception as e:
            logger.error(f"级联删除任务 {task_id} 失败: {e}")
            raise


# 创建全局实例
relation_service = RelationService()
