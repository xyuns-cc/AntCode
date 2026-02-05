"""
统一的项目更新服务
支持在一个事务中更新项目的所有相关数据
"""

from fastapi import HTTPException, status
from loguru import logger
from tortoise.transactions import in_transaction

from src.models import Project, ProjectFile, ProjectRule, ProjectCode, ProjectType
from src.schemas.project_unified import UnifiedProjectUpdateRequest
from src.utils.hash_utils import calculate_content_hash


class UnifiedProjectService:
    """统一项目更新服务"""

    async def _resolve_project(self, project_id, user_id: int, connection=None):
        """解析项目ID（仅 public_id）"""
        if project_id is None:
            return None

        # 检查用户是否为管理员
        from src.services.users.user_service import user_service
        user = await user_service.get_user_by_id(user_id)
        is_admin = user and user.is_admin

        # 通过 public_id 查询
        if is_admin:
            query = Project.filter(public_id=str(project_id))
        else:
            query = Project.filter(public_id=str(project_id), user_id=user_id)
        if connection:
            query = query.using_db(connection)
        return await query.first()

    async def update_project_unified(
        self,
        project_id,
        request: UnifiedProjectUpdateRequest,
        user_id: int
    ):
        """
        统一更新项目 - 在单个事务中处理所有更新
        
        Args:
            project_id: 项目 public_id
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
                        detail="项目不存在或无权限访问"
                    )

                # 2. 更新基本信息
                basic_fields = request.get_basic_fields()
                if basic_fields:
                    basic_fields['updated_by'] = user_id

                    # 处理执行策略相关字段
                    if 'bound_node_id' in basic_fields:
                        bound_node_id = basic_fields['bound_node_id']
                        if bound_node_id:
                            from src.models import Node
                            node = await Node.get_or_none(public_id=str(bound_node_id))
                            if not node:
                                raise HTTPException(status_code=400, detail="绑定节点不存在")
                            basic_fields['bound_node_id'] = node.id
                        else:
                            basic_fields['bound_node_id'] = None

                    await project.update_from_dict(basic_fields)
                    await project.save(using_db=connection)
                    logger.info(f"更新项目基本信息: {project_id}, 字段: {list(basic_fields.keys())}")

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
                detail=f"更新项目失败: {str(e)}"
            )

    async def _update_rule_config(
        self, 
        project_id: int, 
        request: UnifiedProjectUpdateRequest,
        connection
    ):
        """更新规则项目配置"""
        rule_fields = request.get_rule_fields()
        if not rule_fields:
            return

        # 获取现有规则配置
        rule_detail = await ProjectRule.filter(
            project_id=project_id
        ).using_db(connection).first()

        if not rule_detail:
            logger.warning(f"规则项目 {project_id} 的详细配置不存在，跳过规则字段更新")
            return

        # 更新规则配置
        await rule_detail.update_from_dict(rule_fields)
        await rule_detail.save(using_db=connection)
        logger.info(f"更新规则配置: {project_id}, 字段: {list(rule_fields.keys())}")

    async def _update_file_config(
        self, 
        project_id: int, 
        request: UnifiedProjectUpdateRequest,
        connection
    ):
        """更新文件项目配置"""
        file_fields = request.get_file_fields()
        if not file_fields:
            return

        # 获取现有文件配置
        file_detail = await ProjectFile.filter(
            project_id=project_id
        ).using_db(connection).first()

        if not file_detail:
            logger.warning(f"文件项目 {project_id} 的详细配置不存在，跳过文件字段更新")
            return

        # 更新文件配置
        await file_detail.update_from_dict(file_fields)
        await file_detail.save(using_db=connection)
        logger.info(f"更新文件配置: {project_id}, 字段: {list(file_fields.keys())}")

    async def _update_code_config(
        self, 
        project_id: int, 
        request: UnifiedProjectUpdateRequest,
        connection
    ):
        """更新代码项目配置"""
        code_fields = request.get_code_fields()
        if not code_fields:
            return

        # 获取现有代码配置
        code_detail = await ProjectCode.filter(
            project_id=project_id
        ).using_db(connection).first()

        if not code_detail:
            logger.warning(f"代码项目 {project_id} 的详细配置不存在，跳过代码字段更新")
            return

        # 如果更新了代码内容，重新计算哈希
        if 'content' in code_fields:
            content_hash = calculate_content_hash(code_fields['content'])
            code_fields['content_hash'] = content_hash

        # 更新代码配置
        await code_detail.update_from_dict(code_fields)
        await code_detail.save(using_db=connection)
        logger.info(f"更新代码配置: {project_id}, 字段: {list(code_fields.keys())}")


# 创建服务实例
unified_project_service = UnifiedProjectService()
