"""
统一的项目更新服务
支持在一个事务中更新项目的所有相关数据
"""

import hashlib

from fastapi import HTTPException, status
from loguru import logger
from tortoise.transactions import in_transaction

from src.models import Project, ProjectFile, ProjectRule, ProjectCode, ProjectType
from src.schemas.project_unified import UnifiedProjectUpdateRequest


class UnifiedProjectService:
    """统一项目更新服务"""
    
    async def update_project_unified(
        self,
        project_id: int,
        request: UnifiedProjectUpdateRequest,
        user_id: int
    ):
        """
        统一更新项目 - 在单个事务中处理所有更新
        
        Args:
            project_id: 项目ID
            request: 统一更新请求
            user_id: 用户ID
            
        Returns:
            更新后的项目对象
        """
        try:
            async with in_transaction() as connection:
                # 1. 获取项目基本信息
                project = await Project.filter(
                    id=project_id, 
                    user_id=user_id
                ).using_db(connection).first()
                
                if not project:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="项目不存在或无权限访问"
                    )
                
                # 2. 更新基本信息
                basic_fields = request.get_basic_fields()
                if basic_fields:
                    basic_fields['updated_by'] = user_id
                    await project.update_from_dict(basic_fields)
                    await project.save(using_db=connection)
                    logger.info(f"更新项目基本信息: {project_id}, 字段: {list(basic_fields.keys())}")
                
                # 3. 根据项目类型更新详细配置
                if project.type == ProjectType.RULE:
                    await self._update_rule_config(project_id, request, connection)
                elif project.type == ProjectType.FILE:
                    await self._update_file_config(project_id, request, connection)
                elif project.type == ProjectType.CODE:
                    await self._update_code_config(project_id, request, connection)
                
                # 4. 重新获取更新后的项目
                updated_project = await Project.filter(id=project_id).using_db(connection).first()
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
            content_hash = hashlib.md5(code_fields['content'].encode()).hexdigest()
            code_fields['content_hash'] = content_hash
        
        # 更新代码配置
        await code_detail.update_from_dict(code_fields)
        await code_detail.save(using_db=connection)
        logger.info(f"更新代码配置: {project_id}, 字段: {list(code_fields.keys())}")


# 创建服务实例
unified_project_service = UnifiedProjectService()
