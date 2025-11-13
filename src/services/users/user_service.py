"""
用户服务层
处理用户相关的所有业务逻辑，使用统一缓存系统
"""

import hashlib
from datetime import datetime

from loguru import logger
from tortoise.exceptions import IntegrityError

from src.core.cache import user_cache
from src.models.user import User
from src.schemas.common import PaginationInfo
from src.schemas.user import (
    UserResponse,
    UserSimpleResponse,
)


class UserService:
    """用户服务类 - 使用统一缓存系统"""
    
    def __init__(self):
        """初始化用户服务"""
        pass
    
    def _generate_cache_key(self, page, size, is_active=None, is_admin=None, sort_by=None, sort_order=None):
        """生成用户列表缓存键（带namespace前缀）"""
        params = f"p{page}_s{size}"
        if is_active is not None:
            params += f"_a{is_active}"
        if is_admin is not None:
            params += f"_admin{is_admin}"
        if sort_by:
            params += f"_sort{sort_by}"
        if sort_order:
            params += f"_order{sort_order}"
        
        # 生成哈希确保键的唯一性
        cache_hash = hashlib.md5(params.encode()).hexdigest()[:8]
        # 添加namespace前缀，便于按前缀清除
        return f"user:list:{cache_hash}"
    
    async def authenticate_user(self, username, password):
        """
        用户认证
        
        Args:
            username: 用户名
            password: 密码
            
        Returns:
            认证成功返回User对象，否则返回None
        """
        try:
            # 查找用户
            user = await User.get_or_none(username=username)
            if not user:
                logger.debug(f"用户名不存在: {username}")
                return None
            
            # 验证密码
            if user.verify_password(password):
                await self.update_last_login(user.id)
                return user
            else:
                logger.debug(f"用户 {username} 密码验证失败")
                return None
                
        except Exception as e:
            logger.error(f"用户认证失败: {e}")
            return None
    
    async def create_user(self, request):
        """
        创建用户
        
        Args:
            request: 用户创建请求
            
        Returns:
            创建的用户对象
            
        Raises:
            IntegrityError: 用户名已存在
        """
        try:
            # 检查用户名是否已存在
            existing_user = await User.get_or_none(username=request.username)
            if existing_user:
                raise IntegrityError("用户名已存在")
            
            # 检查邮箱是否已存在（如果提供了邮箱）
            if request.email:
                existing_email = await User.get_or_none(email=request.email)
                if existing_email:
                    raise IntegrityError("邮箱已存在")
            
            # 创建用户对象
            user = User(
                username=request.username,
                email=request.email,
                is_active=request.is_active,
                is_admin=request.is_admin
            )
            
            # 设置密码哈希
            user.set_password(request.password)
            
            # 保存用户
            await user.save()
            
            # 清除用户列表缓存
            await self._invalidate_user_cache()
            
            logger.info(f"用户创建成功: {user.username}")
            return user
            
        except IntegrityError:
            raise
        except Exception as e:
            logger.error(f"创建用户失败: {e}")
            raise
    
    async def get_user_by_id(self, user_id):
        """根据ID获取用户"""
        return await User.get_or_none(id=user_id)
    
    async def get_user_by_username(self, username):
        """根据用户名获取用户"""
        return await User.get_or_none(username=username)
    
    async def get_users_by_ids(self, user_ids):
        """根据用户ID列表批量获取用户"""
        if not user_ids:
            return []
        return await User.filter(id__in=user_ids).all()
    
    async def update_last_login(self, user_id):
        """更新用户最后登录时间"""
        try:
            await User.filter(id=user_id).update(last_login_at=datetime.now())
            logger.debug(f"更新用户 {user_id} 最后登录时间")
        except Exception as e:
            logger.error(f"更新最后登录时间失败: {e}")
    
    async def get_users_list(
        self, 
        page = 1, 
        size = 20, 
        is_active = None, 
        is_admin = None,
        sort_by = None,
        sort_order = None
    ):
        """
        获取用户列表（带缓存）
        
        Returns:
            dict: 包含data和pagination的字典
        """
        cache_key = self._generate_cache_key(page, size, is_active, is_admin, sort_by, sort_order)
        
        # 尝试从缓存获取
        try:
            cached_result = await user_cache.get(cache_key)
            if cached_result:
                logger.debug(f"用户列表缓存命中: {cache_key}")
                return cached_result
        except Exception as e:
            # 缓存读取失败，记录日志并继续从数据库查询
            logger.warning(f"用户列表缓存读取失败: {e}，将从数据库查询")
        
        logger.debug(f"从数据库查询用户列表: {cache_key}")
        
        # 构建查询
        query = User.all()
        
        # 添加筛选条件
        if is_active is not None:
            query = query.filter(is_active=is_active)
        if is_admin is not None:
            query = query.filter(is_admin=is_admin)
        
        # 获取总数
        total = await query.count()
        
        # 分页查询
        offset = (page - 1) * size
        allowed_sort_fields = {'id', 'username', 'created_at'}
        order_field = '-created_at'
        if sort_by in allowed_sort_fields:
            order_direction = '-' if (str(sort_order or 'desc').lower() == 'desc') else ''
            order_field = f"{order_direction}{sort_by}"

        query = query.order_by(order_field)
        users = await query.offset(offset).limit(size)
        
        # 转换为响应格式
        user_list = [
            UserResponse(
                id=user.id,
                username=user.username,
                email=user.email,
                is_active=user.is_active,
                is_admin=user.is_admin,
                created_at=user.created_at,
                last_login_at=user.last_login_at
            ) for user in users
        ]
        
        # 计算分页信息
        pages = (total + size - 1) // size
        pagination_info = PaginationInfo(
            page=page,
            size=size,
            total=total,
            pages=pages
        )
        
        # 构造返回结果
        result = {
            'data': {
                'items': user_list,
                'page': page,
                'size': size,
                'total': total,
                'pages': pages
            },
            'pagination': pagination_info
        }
        
        # 缓存结果
        try:
            await user_cache.set(cache_key, result)
        except Exception as e:
            # 缓存写入失败，记录日志但不影响返回结果
            logger.warning(f"用户列表缓存写入失败: {e}")
        
        return result
    
    async def get_simple_user_list(self):
        """
        获取简易用户列表（仅包含ID与用户名）
        """
        cache_key = "user:list:simple"
        
        # 优先尝试从缓存获取
        try:
            cached_users = await user_cache.get(cache_key)
            if cached_users is not None:
                logger.debug("用户简易列表缓存命中")
                return cached_users
        except Exception as e:
            logger.warning(f"读取用户简易列表缓存失败: {e}，将从数据库查询")
        
        # 从数据库查询
        users = await User.all().only("id", "username").order_by("username")
        simple_users = [
            UserSimpleResponse(id=user.id, username=user.username).model_dump()
            for user in users
        ]
        
        # 缓存查询结果
        try:
            await user_cache.set(cache_key, simple_users)
        except Exception as e:
            logger.warning(f"写入用户简易列表缓存失败: {e}")
        
        return simple_users
    
    async def update_user(self, user_id, request):
        """
        更新用户信息
        
        Args:
            user_id: 用户ID
            request: 更新请求
            
        Returns:
            更新后的用户对象
        """
        user = await User.get(id=user_id)
        
        # 更新字段
        update_data = request.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(user, field, value)
        
        await user.save()
        
        # 清除缓存
        await self._invalidate_user_cache(user_id)
        
        logger.info(f"用户信息更新成功: {user.username}")
        return user
    
    async def update_user_password(
        self, 
        user_id, 
        request, 
        current_user_id
    ):
        """
        更新用户密码
        
        Args:
            user_id: 目标用户ID
            request: 密码更新请求
            current_user_id: 当前用户ID
        """
        user = await User.get(id=user_id)
        current_user = await User.get(id=current_user_id)
        
        # 权限检查
        if user_id != current_user_id and not current_user.is_admin:
            raise PermissionError("无权限修改其他用户密码")
        
        # 如果是修改自己的密码，需要验证当前密码
        if user_id == current_user_id and request.current_password:
            if not user.verify_password(request.current_password):
                raise ValueError("当前密码不正确")
        
        # 更新密码
        user.set_password(request.new_password)
        await user.save()
        
        logger.info(f"用户 {user.username} 密码更新成功")
    
    async def reset_user_password(self, user_id, new_password):
        """
        重置用户密码（管理员专用）
        
        Args:
            user_id: 目标用户ID
            new_password: 新密码
        """
        user = await User.get(id=user_id)
        
        # 更新密码
        user.set_password(new_password)
        await user.save()
        
        logger.info(f"用户 {user.username} 密码重置成功")
    
    async def delete_user(self, user_id, current_user_id):
        """
        删除用户
        
        Args:
            user_id: 要删除的用户ID
            current_user_id: 当前用户ID
        """
        user = await User.get(id=user_id)
        
        # 不能删除自己
        if user_id == current_user_id:
            raise ValueError("不能删除自己")
        
        # 检查权限
        current_user = await User.get(id=current_user_id)
        if not current_user.is_admin:
            raise PermissionError("只有管理员可以删除用户")
        
        # 删除用户
        await user.delete()
        
        # 清除缓存
        await self._invalidate_user_cache(user_id)
        
        logger.info(f"用户删除成功: {user.username}")
    
    async def _invalidate_user_cache(self, user_id = None):
        """清除用户相关缓存（按前缀清除，不影响其他命名空间）"""
        try:
            # 只清除user:前缀的缓存
            await user_cache.clear_prefix("user:")
            logger.debug("用户缓存已清除（按前缀）")
        except Exception as e:
            logger.error(f"清除用户缓存失败: {e}")
    
    async def get_cache_info(self):
        """获取缓存信息"""
        return await user_cache.get_stats()
    
    async def clear_cache(self):
        """清空用户相关缓存（按前缀）"""
        await user_cache.clear_prefix("user:")
        logger.info("用户缓存已清空")


# 全局用户服务实例
user_service = UserService()