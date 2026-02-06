"""
统一的项目更新服务
支持在一个事务中更新项目的所有相关数据
"""

from fastapi import HTTPException, status
from loguru import logger
from tortoise.transactions import in_transaction

from antcode_core.common.hash_utils import calculate_content_hash
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

                    # 处理执行策略相关字段
                    if "bound_worker_id" in basic_fields:
                        bound_worker_id = basic_fields["bound_worker_id"]
                        if bound_worker_id:
                            # 将 public_id 转换为内部 id
                            from antcode_core.domain.models import Worker

                            worker = await Worker.get_or_none(public_id=str(bound_worker_id))
                            if worker:
                                basic_fields["bound_worker_id"] = worker.id
                            else:
                                # 尝试直接作为内部 id 使用
                                try:
                                    basic_fields["bound_worker_id"] = int(bound_worker_id)
                                except (ValueError, TypeError):
                                    basic_fields["bound_worker_id"] = None
                        else:
                            basic_fields["bound_worker_id"] = None

                    await project.update_from_dict(basic_fields)
                    await project.save(using_db=connection)
                    logger.info(
                        f"更新项目基本信息: {project_id}, 字段: {list(basic_fields.keys())}"
                    )

                # 3. 根据项目类型更新详细配置（使用内部 ID）
                if project.type == ProjectType.RULE:
                    await self._update_rule_config(project.id, request, connection)
                elif project.type == ProjectType.FILE:
                    await self._update_file_config(project.id, request, connection)
                elif project.type == ProjectType.CODE:
                    await self._update_code_config(project.id, request, connection)

                # 4. 重新获取更新后的项目（使用内部 ID）
                updated_project = await Project.filter(id=project.id).using_db(connection).first()
                return updated_project

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"统一更新项目失败: {project_id}, 错误: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"更新项目失败: {str(e)}",
            )

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

        # 如果更新了代码内容，重新计算哈希
        if "content" in code_fields:
            content_hash = calculate_content_hash(code_fields["content"])
            code_fields["content_hash"] = content_hash

        # 更新代码配置
        await code_detail.update_from_dict(code_fields)
        await code_detail.save(using_db=connection)
        logger.info(f"更新代码配置: {project_id}, 字段: {list(code_fields.keys())}")


# 创建服务实例
unified_project_service = UnifiedProjectService()
