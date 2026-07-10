"""应用层关联关系管理服务"""

from loguru import logger
from tortoise.exceptions import DoesNotExist
from tortoise.transactions import in_transaction

from antcode_core.application.services.base import QueryHelper
from antcode_core.domain.models.project import Project, ProjectCode, ProjectFile, ProjectRule
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.domain.models.user import User


class RelationService:
    """应用层关联关系管理器"""

    @staticmethod
    async def _get_by_id_or_public_id(model_class, id_value):
        """通用的 ID/public_id 查询"""
        try:
            internal_id = int(id_value)
            return await model_class.get(id=internal_id)
        except (ValueError, TypeError):
            try:
                return await model_class.get(public_id=str(id_value))
            except DoesNotExist:
                return None
        except DoesNotExist:
            return None

    @staticmethod
    async def get_user_by_id(user_id):
        """根据ID获取用户"""
        return await User.get_or_none(id=user_id)

    @staticmethod
    async def get_project_by_id(project_id):
        """根据ID获取项目（支持 public_id）"""
        return await RelationService._get_by_id_or_public_id(Project, project_id)

    @staticmethod
    async def get_task_by_id(task_id):
        """根据ID获取任务"""
        return await Task.get_or_none(id=task_id)

    @staticmethod
    async def get_execution_by_id(run_id):
        """根据ID获取执行记录"""
        return await TaskRun.get_or_none(id=run_id)

    # ==================== 项目关联关系 ====================

    @staticmethod
    async def get_project_with_user(project_id):
        """获取项目及其创建者信息"""
        project = await RelationService.get_project_by_id(project_id)
        if not project:
            return None

        user = await RelationService.get_user_by_id(project.user_id)

        return {"project": project, "user": user}

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

        # 使用项目的内部 ID 查询详情（详情表的 project_id 是内部整数 ID）
        internal_id = project.id

        # 根据项目类型获取对应的详情
        detail = None
        if project.type == "file":
            detail = await RelationService.get_project_file_detail(internal_id)
        elif project.type == "rule":
            detail = await RelationService.get_project_rule_detail(internal_id)
        elif project.type == "code":
            detail = await RelationService.get_project_code_detail(internal_id)

        return {"project": project, "user": user, "detail": detail}

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
            "user": user,
        }

    @staticmethod
    async def get_project_tasks(project_id):
        """获取项目的所有任务"""
        return await Task.filter(project_id=project_id).all()

    @staticmethod
    async def get_user_tasks(user_id):
        """获取用户的所有任务"""
        return await Task.filter(user_id=user_id).all()

    # ==================== 执行记录关联关系 ====================

    @staticmethod
    async def get_execution_with_task(run_id):
        """获取执行记录及其关联的任务信息"""
        execution = await RelationService.get_execution_by_id(run_id)
        if not execution:
            return None

        task_info = await RelationService.get_task_with_project(execution.task_id)

        return {
            "execution": execution,
            "task": task_info["task"] if task_info else None,
            "project": task_info["project"] if task_info else None,
            "project_detail": task_info["project_detail"] if task_info else None,
        }

    @staticmethod
    async def get_task_executions(task_id):
        """获取任务的所有执行记录"""
        return await TaskRun.filter(task_id=task_id).order_by("-created_at").all()

    # ==================== 数据完整性检查 ====================

    @staticmethod
    async def validate_project_user(project_id, user_id):
        """验证项目是否属于指定用户（管理员可访问所有项目）"""
        project = await RelationService.get_project_by_id(project_id)
        if project is None:
            return False

        # 检查是否为管理员（使用 QueryHelper.is_admin）
        if await QueryHelper.is_admin(user_id):
            return True

        return project.user_id == user_id

    @staticmethod
    async def validate_task_user(task_id, user_id):
        """验证任务是否属于指定用户"""
        task = await RelationService.get_task_by_id(task_id)
        if not task:
            return False

        # 通过项目验证用户权限
        return await RelationService.validate_project_user(task.project_id, user_id)

    @staticmethod
    async def validate_execution_user(run_id, user_id):
        """验证执行记录是否属于指定用户"""
        execution = await RelationService.get_execution_by_id(run_id)
        if not execution:
            return False

        # 通过任务验证用户权限
        return await RelationService.validate_task_user(execution.task_id, user_id)

    # ==================== 级联删除操作 ====================

    @staticmethod
    async def delete_project_cascade(project_id):
        """级联删除项目及其相关数据

        P1-20 修复要点:
        - 全流程包裹在 `in_transaction()` 里,任一步骤失败整体回滚,不会
          出现"任务清了、项目还在"的半状态。
        - 补漏此前遗漏的子表:
          * ProjectSource (project_id 唯一, 一对一)
          * RunSourceSnapshot (project_id 索引, 一对多)
        - GitCredential **不**跟随项目删除:它 owner_user_id 归属用户,
          可能被同一用户的多个项目复用,由 user_service.delete_user 负责清理。
        """
        from antcode_core.domain.models import ProjectRuntimeBinding, ProjectSource, RunSourceSnapshot

        deleted = {
            "tasks": 0,
            "executions": 0,
            "details": 0,
            "runtime_bindings": 0,
            "project_sources": 0,
            "run_source_snapshots": 0,
        }

        try:
            async with in_transaction():
                # 1. 任务 → 执行记录(先删 child 避免悬挂)
                task_ids = await Task.filter(project_id=project_id).values_list("id", flat=True)
                if task_ids:
                    deleted["executions"] = await TaskRun.filter(
                        task_id__in=list(task_ids)
                    ).delete()

                # 2. 任务本体
                deleted["tasks"] = await Task.filter(project_id=project_id).delete()

                # 3. 项目详情(file / rule / code 至多命中其一)
                #    事务内不并发,顺序执行以避免共享同一连接时的并发冲突
                d_file = await ProjectFile.filter(project_id=project_id).delete()
                d_rule = await ProjectRule.filter(project_id=project_id).delete()
                d_code = await ProjectCode.filter(project_id=project_id).delete()
                deleted["details"] = d_file + d_rule + d_code

                # 4. 运行时绑定
                deleted["runtime_bindings"] = await ProjectRuntimeBinding.filter(
                    project_id=project_id
                ).delete()

                # 5. 补漏 - ProjectSource(Git 源配置, 一对一)
                deleted["project_sources"] = await ProjectSource.filter(
                    project_id=project_id
                ).delete()

                # 6. 补漏 - RunSourceSnapshot(任务运行时的源码快照, 一对多)
                deleted["run_source_snapshots"] = await RunSourceSnapshot.filter(
                    project_id=project_id
                ).delete()

                # 7. 项目本体
                await Project.filter(id=project_id).delete()

            logger.info(f"级联删除项目 {project_id}: {deleted}")
            return deleted
        except Exception as e:
            logger.error(f"级联删除项目 {project_id} 失败(事务已回滚): {e}")
            raise

    @staticmethod
    async def delete_task_cascade(task_id):
        """级联删除任务及其相关数据"""
        deleted_counts = {"executions": 0}

        try:
            async with in_transaction():
                # 1. 执行记录
                deleted_counts["executions"] = await TaskRun.filter(task_id=task_id).delete()

                # 2. 任务本体
                await Task.filter(id=task_id).delete()

            logger.info(f"级联删除任务 {task_id} 完成: {deleted_counts}")
            return deleted_counts

        except Exception as e:
            logger.error(f"级联删除任务 {task_id} 失败(事务已回滚): {e}")
            raise


# 创建全局实例
relation_service = RelationService()
