"""用户服务"""
import hashlib
from datetime import datetime

from loguru import logger
from tortoise.exceptions import IntegrityError

from src.core.cache import user_cache
from src.models.user import User
from src.schemas.common import PaginationInfo
from src.schemas.user import UserResponse, UserSimpleResponse
from src.utils.redis_pool import redis_pool


class UserService:
    """用户服务"""
    
    def __init__(self):
        pass
    
    def _generate_cache_key(self, page, size, is_active=None, is_admin=None, sort_by=None, sort_order=None):
        params = f"p{page}_s{size}"
        if is_active is not None:
            params += f"_a{is_active}"
        if is_admin is not None:
            params += f"_admin{is_admin}"
        if sort_by:
            params += f"_sort{sort_by}"
        if sort_order:
            params += f"_order{sort_order}"
        
        cache_hash = hashlib.md5(params.encode()).hexdigest()[:8]
        return f"user:list:{cache_hash}"
    
    async def authenticate_user(self, username, password):
        try:
            user = await User.get_or_none(username=username)
            if not user:
                logger.debug(f"用户不存在: {username}")
                return None
            
            if user.verify_password(password):
                await self.update_last_login(user.id)
                return user
            else:
                logger.debug(f"密码验证失败: {username}")
                return None
                
        except Exception as e:
            logger.error(f"认证失败: {e}")
            return None
    
    async def create_user(self, request):
        try:
            existing_user = await User.get_or_none(username=request.username)
            if existing_user:
                raise IntegrityError("用户名已存在")
            
            if request.email:
                existing_email = await User.get_or_none(email=request.email)
                if existing_email:
                    raise IntegrityError("邮箱已存在")
            
            user = User(
                username=request.username,
                email=request.email,
                is_active=request.is_active,
                is_admin=request.is_admin
            )
            
            user.set_password(request.password)
            await user.save()
            await self._invalidate_user_cache()
            
            logger.info(f"用户已创建: {user.username}")
            return user
            
        except IntegrityError:
            raise
        except Exception as e:
            logger.error(f"创建用户失败: {e}")
            raise
    
    async def get_user_by_id(self, user_id):
        return await User.get_or_none(id=user_id)
    
    async def get_user_by_username(self, username):
        return await User.get_or_none(username=username)
    
    async def get_users_by_ids(self, user_ids):
        if not user_ids:
            return []
        return await User.filter(id__in=user_ids).all()
    
    async def is_admin(self, user_id) -> bool:
        """检查用户是否为管理员（带缓存）"""
        cache_key = f"user:admin:{user_id}"
        try:
            cached = await redis_pool.get(cache_key)
            if cached is not None:
                return cached == "1"
        except Exception:
            pass
        
        user = await User.get_or_none(id=user_id).only('id', 'is_admin')
        is_admin = bool(user and user.is_admin)
        
        try:
            await redis_pool.set(cache_key, "1" if is_admin else "0", ex=300)
        except Exception:
            pass
        
        return is_admin
    
    async def update_last_login(self, user_id):
        try:
            await User.filter(id=user_id).update(last_login_at=datetime.now())
            logger.debug(f"更新最后登录时间: user_id={user_id}")
        except Exception as e:
            logger.error(f"更新最后登录时间失败: {e}")
    
    async def get_users_list(self, page=1, size=20, is_active=None, is_admin=None, sort_by=None, sort_order=None):
        cache_key = self._generate_cache_key(page, size, is_active, is_admin, sort_by, sort_order)
        
        try:
            cached_result = await user_cache.get(cache_key)
            if cached_result:
                logger.debug(f"用户列表缓存命中: {cache_key}")
                return cached_result
        except Exception as e:
            logger.warning(f"缓存读取失败: {e}")
        
        logger.debug(f"从数据库查询用户列表: {cache_key}")
        
        query = User.all()
        
        if is_active is not None:
            query = query.filter(is_active=is_active)
        if is_admin is not None:
            query = query.filter(is_admin=is_admin)
        
        total = await query.count()
        
        offset = (page - 1) * size
        allowed_sort_fields = {'id', 'username', 'created_at'}
        order_field = '-created_at'
        if sort_by in allowed_sort_fields:
            order_direction = '-' if (str(sort_order or 'desc').lower() == 'desc') else ''
            order_field = f"{order_direction}{sort_by}"

        query = query.order_by(order_field)
        users = await query.offset(offset).limit(size)
        
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
        
        pages = (total + size - 1) // size
        pagination_info = PaginationInfo(page=page, size=size, total=total, pages=pages)
        
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
        
        try:
            await user_cache.set(cache_key, result)
        except Exception as e:
            logger.warning(f"缓存写入失败: {e}")
        
        return result
    
    async def get_simple_user_list(self):
        cache_key = "user:list:simple"
        
        try:
            cached_users = await user_cache.get(cache_key)
            if cached_users is not None:
                logger.debug("简单用户列表缓存命中")
                return cached_users
        except Exception as e:
            logger.warning(f"缓存读取失败: {e}")
        
        users = await User.all().only("id", "username").order_by("username")
        simple_users = [
            UserSimpleResponse(id=user.id, username=user.username).model_dump()
            for user in users
        ]
        
        try:
            await user_cache.set(cache_key, simple_users)
        except Exception as e:
            logger.warning(f"缓存写入失败: {e}")
        
        return simple_users
    
    async def update_user(self, user_id, request):
        user = await User.get(id=user_id)
        
        update_data = request.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(user, field, value)
        
        await user.save()
        await self._invalidate_user_cache(user_id)
        
        logger.info(f"用户已更新: {user.username}")
        return user
    
    async def update_user_password(self, user_id, request, current_user_id):
        user = await User.get(id=user_id)
        current_user = await User.get(id=current_user_id)
        
        if user_id != current_user_id and not current_user.is_admin:
            raise PermissionError("无权修改其他用户的密码")
        
        if user_id == current_user_id and request.current_password:
            if not user.verify_password(request.current_password):
                raise ValueError("当前密码错误")
        
        user.set_password(request.new_password)
        await user.save()
        
        logger.info(f"密码已更新: {user.username}")
    
    async def reset_user_password(self, user_id, new_password):
        user = await User.get(id=user_id)
        
        user.set_password(new_password)
        await user.save()
        
        logger.info(f"密码已重置: {user.username}")
    
    async def delete_user(self, user_id, current_user_id):
        user = await User.get(id=user_id)
        
        if user_id == current_user_id:
            raise ValueError("不能删除自己")
        
        current_user = await User.get(id=current_user_id)
        if not current_user.is_admin:
            raise PermissionError("仅管理员可删除用户")
        
        await user.delete()
        await self._invalidate_user_cache(user_id)
        
        logger.info(f"用户已删除: {user.username}")
    
    async def _invalidate_user_cache(self, user_id=None):
        try:
            await user_cache.clear_prefix("user:")
            logger.debug("用户缓存已清除")
        except Exception as e:
            logger.error(f"清除用户缓存失败: {e}")
    
    async def get_cache_info(self):
        return await user_cache.get_stats()
    
    async def clear_cache(self):
        await user_cache.clear_prefix("user:")
        logger.info("用户缓存已清除")


user_service = UserService()
