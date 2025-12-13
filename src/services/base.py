"""服务层基础类"""
from typing import TypeVar, Any
from loguru import logger

T = TypeVar('T')


class QueryHelper:
    """查询辅助工具"""

    @staticmethod
    async def get_by_id_or_public_id(model_class, id_value, user_id: int = None, check_admin: bool = True):
        """
        通过 ID 或 public_id 获取对象
        
        Args:
            model_class: 模型类
            id_value: ID 值（支持内部 ID 或 public_id）
            user_id: 用户 ID（用于权限过滤）
            check_admin: 是否检查管理员权限
        """
        # 确定查询字段
        try:
            internal_id = int(id_value)
            query = model_class.filter(id=internal_id)
        except (ValueError, TypeError):
            query = model_class.filter(public_id=str(id_value))

        # 权限过滤
        if user_id is not None and check_admin:
            from src.services.users.user_service import user_service
            user = await user_service.get_user_by_id(user_id)
            if not (user and user.is_admin):
                query = query.filter(user_id=user_id)

        return await query.first()

    @staticmethod
    async def is_admin(user_id: int) -> bool:
        """检查用户是否为管理员"""
        from src.services.users.user_service import user_service
        user = await user_service.get_user_by_id(user_id)
        return user and user.is_admin

    @staticmethod
    async def batch_get_user_info(user_ids: list[int]) -> dict[int, dict]:
        """批量获取用户信息"""
        if not user_ids:
            return {}

        from src.models import User
        users = await User.filter(id__in=user_ids).only('id', 'username', 'public_id')
        return {u.id: {'username': u.username, 'public_id': u.public_id} for u in users}

    @staticmethod
    async def batch_get_project_public_ids(project_ids: list[int]) -> dict[int, str]:
        """批量获取项目 public_id"""
        if not project_ids:
            return {}

        from src.models import Project
        projects = await Project.filter(id__in=project_ids).only('id', 'public_id')
        return {p.id: p.public_id for p in projects}

    @staticmethod
    async def batch_get_node_info(node_ids: list[int]) -> dict[int, dict]:
        """批量获取节点信息"""
        if not node_ids:
            return {}

        from src.models import Node
        nodes = await Node.filter(id__in=node_ids).only('id', 'public_id', 'name')
        return {n.id: {'public_id': n.public_id, 'name': n.name} for n in nodes}

    @staticmethod
    async def paginate(query, page: int, size: int, order_by: str = '-created_at') -> tuple[list, int]:
        """
        统一分页查询
        
        Args:
            query: Tortoise ORM 查询对象
            page: 页码（从 1 开始）
            size: 每页数量
            order_by: 排序字段，前缀 '-' 表示降序，默认 '-created_at'
        
        Returns:
            (items, total) 元组，items 为查询结果列表，total 为总数
        """
        total = await query.count()
        offset = (page - 1) * size
        items = await query.order_by(order_by).offset(offset).limit(size)
        return items, total


class BaseService:
    """服务基类"""

    def __init__(self):
        self.query = QueryHelper()

    async def _check_permission(self, user_id: int, resource_user_id: int) -> bool:
        """检查用户是否有权限访问资源"""
        if user_id == resource_user_id:
            return True
        return await self.query.is_admin(user_id)

    def _log_operation(self, operation: str, resource_id: Any, user_id: int = None):
        """记录操作日志"""
        user_info = f" by user {user_id}" if user_id else ""
        logger.info(f"{operation}: {resource_id}{user_info}")


# 全局查询辅助实例
query_helper = QueryHelper()
