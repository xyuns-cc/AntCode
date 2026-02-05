"""用户服务"""
import re
from datetime import datetime
from typing import Optional, Tuple

from loguru import logger

from src.utils.hash_utils import calculate_content_hash
from tortoise.exceptions import IntegrityError

from src.infrastructure.cache import user_cache
from src.models.user import User
from src.schemas.common import PaginationInfo
from src.schemas.user import UserResponse, UserSimpleResponse
from src.core.config import settings


def validate_password_strength(password: str) -> Tuple[bool, str]:
    """
    验证密码强度
    
    要求:
    - 最少 8 个字符
    - 至少包含一个大写字母
    - 至少包含一个小写字母
    - 至少包含一个数字
    - 至少包含一个特殊字符
    
    Returns:
        (is_valid, error_message)
    """
    if len(password) < 8:
        return False, "密码长度至少为 8 个字符"
    
    if not re.search(r'[A-Z]', password):
        return False, "密码必须包含至少一个大写字母"
    
    if not re.search(r'[a-z]', password):
        return False, "密码必须包含至少一个小写字母"
    
    if not re.search(r'\d', password):
        return False, "密码必须包含至少一个数字"
    
    if not re.search(r'[!@#$%^&*(),.?":{}|<>_\-+=\[\]\\;\'`~]', password):
        return False, "密码必须包含至少一个特殊字符"
    
    return True, ""


class UserService:
    """用户服务"""

    # 允许排序的字段
    ALLOWED_SORT_FIELDS = {'id', 'username', 'created_at'}

    def _generate_cache_key(
        self,
        page: int,
        size: int,
        is_active: Optional[bool] = None,
        is_admin: Optional[bool] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None
    ) -> str:
        """生成缓存键"""
        parts = [f"p{page}", f"s{size}"]
        if is_active is not None:
            parts.append(f"a{is_active}")
        if is_admin is not None:
            parts.append(f"admin{is_admin}")
        if sort_by:
            parts.append(f"sort{sort_by}")
        if sort_order:
            parts.append(f"order{sort_order}")
        return f"user:list:{calculate_content_hash('_'.join(parts))[:8]}"

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

            # 验证密码强度
            is_valid, error_msg = validate_password_strength(request.password)
            if not is_valid:
                raise ValueError(error_msg)

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

    async def get_user_by_public_id(self, public_id: str):
        """通过 public_id 获取用户"""
        return await User.get_or_none(public_id=public_id)

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
            from src.infrastructure.redis import redis_pool
            cached = await redis_pool.get(cache_key)
            if cached is not None:
                return cached == "1"
        except Exception:
            pass

        user = await User.get_or_none(id=user_id).only('id', 'is_admin')
        is_admin = bool(user and user.is_admin)

        try:
            from src.infrastructure.redis import redis_pool
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

    async def get_users_list(
        self,
        page: int = 1,
        size: int = 20,
        is_active: Optional[bool] = None,
        is_admin: Optional[bool] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None
    ) -> dict:
        """获取用户列表"""

        # 构建查询
        query = User.all()
        if is_active is not None:
            query = query.filter(is_active=is_active)
        if is_admin is not None:
            query = query.filter(is_admin=is_admin)

        total = await query.count()

        # 排序
        order_field = '-created_at'
        if sort_by in self.ALLOWED_SORT_FIELDS:
            prefix = '-' if str(sort_order or 'desc').lower() == 'desc' else ''
            order_field = f"{prefix}{sort_by}"

        users = await query.order_by(order_field).offset((page - 1) * size).limit(size)

        from src.services.sessions.session_service import user_session_service
        online_ids = await user_session_service.get_online_user_ids([u.id for u in users])

        # 构建响应
        user_list = []
        for u in users:
            user_list.append(
                UserResponse(
                    id=u.public_id,
                    username=u.username,
                    email=u.email,
                    is_active=u.is_active,
                    is_admin=u.is_admin,
                    is_super_admin=bool(u.is_admin and u.username == settings.DEFAULT_ADMIN_USERNAME),
                    is_online=u.id in online_ids,
                    created_at=u.created_at,
                    last_login_at=u.last_login_at,
                )
            )

        pages = (total + size - 1) // size
        result = {
            'data': {'items': user_list, 'page': page, 'size': size, 'total': total, 'pages': pages},
            'pagination': PaginationInfo(page=page, size=size, total=total, pages=pages)
        }

        return result

    async def get_simple_user_list(self) -> list:
        """获取简易用户列表（带缓存）"""
        cache_key = "user:list:simple"

        try:
            if cached := await user_cache.get(cache_key):
                return cached
        except Exception:
            pass

        users = await User.all().order_by("username")
        result = [UserSimpleResponse(id=u.public_id, username=u.username).model_dump() for u in users]

        try:
            await user_cache.set(cache_key, result)
        except Exception:
            pass

        return result

    async def update_user(self, user_id, request):
        """更新用户（仅 public_id）"""
        user = await self.get_user_by_public_id(user_id)
        if not user:
            raise ValueError("用户不存在")

        update_data = request.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(user, field, value)

        await user.save()
        await self._invalidate_user_cache(user.id)

        logger.info(f"用户已更新: {user.username}")
        return user

    async def update_user_password(self, user_id, request, current_user_id):
        """更新用户密码（仅 public_id）"""
        user = await self.get_user_by_public_id(user_id)
        if not user:
            raise ValueError("用户不存在")

        current_user = await User.get(id=current_user_id)

        if user.id != current_user_id and not current_user.is_admin:
            raise PermissionError("无权修改其他用户的密码")

        if user.id == current_user_id and request.old_password:
            if not user.verify_password(request.old_password):
                raise ValueError("当前密码错误")

        # 验证新密码强度
        is_valid, error_msg = validate_password_strength(request.new_password)
        if not is_valid:
            raise ValueError(error_msg)

        user.set_password(request.new_password)
        await user.save()

        logger.info(f"密码已更新: {user.username}")

    async def reset_user_password(self, user_id, new_password):
        """重置用户密码（仅 public_id）"""
        user = await self.get_user_by_public_id(user_id)
        if not user:
            raise ValueError("用户不存在")

        # 验证新密码强度
        is_valid, error_msg = validate_password_strength(new_password)
        if not is_valid:
            raise ValueError(error_msg)

        user.set_password(new_password)
        await user.save()

        logger.info(f"密码已重置: {user.username}")

    async def delete_user(self, user_id, current_user_id):
        """删除用户（仅 public_id，级联删除关联数据）"""
        user = await self.get_user_by_public_id(user_id)
        if not user:
            raise ValueError("用户不存在")

        if user.id == current_user_id:
            raise ValueError("不能删除自己")

        current_user = await User.get(id=current_user_id)
        if not current_user.is_admin:
            raise PermissionError("仅管理员可删除用户")

        # 级联删除用户关联数据
        deleted_counts = await self._cascade_delete_user_data(user.id)

        await user.delete()
        await self._invalidate_user_cache(user.id)

        logger.info(f"用户已删除: {user.username}, 级联删除: {deleted_counts}")

    async def _cascade_delete_user_data(self, user_id: int) -> dict:
        """级联删除用户的所有关联数据"""
        from src.models import UserNodePermission

        deleted = {
            "node_permissions": 0,
        }

        try:
            # 删除用户节点权限
            deleted["node_permissions"] = await UserNodePermission.filter(user_id=user_id).delete()
        except Exception as e:
            logger.error(f"级联删除用户数据失败: {e}")
            raise

        return deleted

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
