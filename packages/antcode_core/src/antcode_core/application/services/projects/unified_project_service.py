"""
统一的项目更新服务
支持在一个事务中更新项目的所有相关数据
"""

from fastapi import HTTPException, status
from loguru import logger
from tortoise.transactions import in_transaction

from antcode_core.domain.models import Project, ProjectCode, ProjectFile, ProjectRule, ProjectType


class UnifiedProjectService:
    """统一项目更新服务"""

    async def _resolve_project(self, project_id, user_id, connection=None):
        """解析项目ID（支持 public_id 和内部 id）"""
        if project_id is None:
            return None

        # 检查用户是否为管理员
        from antcode_core.application.services.users.user_service import user_service

        user = await user_service.get_user_by_id(user_id)
        is_admin = user and user.is_admin

        # 尝试作为整数（内部ID）
        try:
            internal_id = int(project_id)
            query = Project.filter(id=internal_id) if is_admin else Project.filter(id=internal_id, user_id=user_id)
            if connection:
                query = query.using_db(connection)
            project = await query.first()
            if project:
                return project
        except (ValueError, TypeError):
            pass

        # 通过 public_id 查询
        if is_admin:
            query = Project.filter(public_id=str(project_id))
        else:
            query = Project.filter(public_id=str(project_id), user_id=user_id)
        if connection:
            query = query.using_db(connection)
        return await query.first()

    async def update_project_unified(self, project_id, request, user_id):
        """
        统一更新项目 - 在单个事务中处理所有更新

        Args:
            project_id: 项目ID或public_id
            request: 统一更新请求
            user_id: 用户ID

        Returns:
            更新后的项目对象
        """
        try:
            async with in_transaction() as connection:
                # 1. 获取项目基本信息（支持 public_id）
                project = await self._resolve_project(project_id, user_id, connection)

                if not project:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="项目不存在或无权限访问",
                    )

                # 2. 更新基本信息
                basic_fields = request.get_basic_fields()
                if basic_fields:
                    basic_fields["updated_by"] = user_id
                    await self._resolve_bound_worker(basic_fields)
                    await project.update_from_dict(basic_fields)
                    await project.save(using_db=connection)
                    logger.info(f"更新项目基本信息: {project_id}, 字段: {list(basic_fields.keys())}")

                await self._update_type_config(project, request, connection)
                updated_project = await Project.filter(id=project.id).using_db(connection).first()
                # S10 (P2 兄弟修复): _resolve_public_id 要求响应对象带
                # created_by_public_id/username 快照，否则 ProjectResponseBuilder
                # 会抛 500。update 端点历史上一直漏挂，PUT 一改就报"响应对象缺
                # 少 created_by_public_id"。这里对齐 create_project 的做法。
                await self._attach_creator(updated_project)
                return updated_project

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"统一更新项目失败: {project_id}, 错误: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"更新项目失败: {str(e)}",
            )

    @staticmethod
    async def _resolve_bound_worker(basic_fields):
        if "bound_worker_id" not in basic_fields:
            return
        bound_worker_id = basic_fields["bound_worker_id"]
        if not bound_worker_id:
            basic_fields["bound_worker_id"] = None
            return
        from antcode_core.domain.models import Worker

        worker = await Worker.get_or_none(public_id=str(bound_worker_id))
        if worker:
            basic_fields["bound_worker_id"] = worker.id
            return
        try:
            basic_fields["bound_worker_id"] = int(bound_worker_id)
        except (ValueError, TypeError):
            basic_fields["bound_worker_id"] = None

    async def _update_type_config(self, project, request, connection):
        handlers = {
            ProjectType.RULE: self._update_rule_config,
            ProjectType.FILE: self._update_file_config,
            ProjectType.CODE: self._update_code_config,
        }
        handler = handlers.get(project.type)
        if handler:
            await handler(project.id, request, connection)

    @staticmethod
    async def _attach_creator(project):
        if project is None:
            return
        from antcode_core.application.services.users.user_service import user_service

        creator = await user_service.get_user_by_id(project.user_id)
        project.created_by_public_id = creator.public_id if creator else None
        project.created_by_username = creator.username if creator else None

    async def _update_rule_config(self, project_id, request, connection):
        """更新规则项目配置"""
        rule_fields = request.get_rule_fields()
        if not rule_fields:
            return

        # 获取现有规则配置
        rule_detail = await ProjectRule.filter(project_id=project_id).using_db(connection).first()

        if not rule_detail:
            logger.warning(f"规则项目 {project_id} 的详细配置不存在，跳过规则字段更新")
            return

        # 更新规则配置
        await rule_detail.update_from_dict(rule_fields)
        await rule_detail.save(using_db=connection)
        logger.info(f"更新规则配置: {project_id}, 字段: {list(rule_fields.keys())}")

    async def _update_file_config(self, project_id, request, connection):
        """更新文件项目配置"""
        file_fields = request.get_file_fields()
        if not file_fields:
            return

        # 获取现有文件配置
        file_detail = await ProjectFile.filter(project_id=project_id).using_db(connection).first()

        if not file_detail:
            logger.warning(f"文件项目 {project_id} 的详细配置不存在，跳过文件字段更新")
            return

        # 更新文件配置
        await file_detail.update_from_dict(file_fields)
        await file_detail.save(using_db=connection)
        logger.info(f"更新文件配置: {project_id}, 字段: {list(file_fields.keys())}")

    async def _update_code_config(self, project_id, request, connection):
        """更新代码项目配置"""
        code_fields = request.get_code_fields()
        if not code_fields:
            return

        # 获取现有代码配置
        code_detail = await ProjectCode.filter(project_id=project_id).using_db(connection).first()

        if not code_detail:
            logger.warning(f"代码项目 {project_id} 的详细配置不存在，跳过代码字段更新")
            return

        # 更新代码配置
        await code_detail.update_from_dict(code_fields)
        await code_detail.save(using_db=connection)
        logger.info(f"更新代码配置: {project_id}, 字段: {list(code_fields.keys())}")


# 创建服务实例
unified_project_service = UnifiedProjectService()
