"""用户服务"""

import contextlib
import re
from datetime import datetime

from loguru import logger
from tortoise.exceptions import IntegrityError

from antcode_core.common.hash_utils import calculate_content_hash
from antcode_core.domain.models.user import User
from antcode_core.domain.schemas.common import PaginationInfo
from antcode_core.domain.schemas.user import UserResponse, UserSimpleResponse
from antcode_core.infrastructure.cache import user_cache


def validate_password_strength(password):
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

    if not re.search(r"[A-Z]", password):
        return False, "密码必须包含至少一个大写字母"

    if not re.search(r"[a-z]", password):
        return False, "密码必须包含至少一个小写字母"

    if not re.search(r"\d", password):
        return False, "密码必须包含至少一个数字"

    if not re.search(r'[!@#$%^&*(),.?":{}|<>_\-+=\[\]\\;\'`~]', password):
        return False, "密码必须包含至少一个特殊字符"

    return True, ""


class UserService:
    """用户服务"""

    # 允许排序的字段
    ALLOWED_SORT_FIELDS = {"id", "username", "created_at"}
    ONLINE_WINDOW_SECONDS = 900

    @staticmethod
    def _is_user_online(last_login_at: datetime | None) -> bool:
        if not last_login_at:
            return False

        now = datetime.now(last_login_at.tzinfo) if last_login_at.tzinfo else datetime.now()
        return (now - last_login_at).total_seconds() <= UserService.ONLINE_WINDOW_SECONDS

    def _normalize_cached_user_list(self, cached):
        """修复历史缓存字段缺失问题（updated_at）"""
        try:
            items = cached.get("data", {}).get("items", [])
        except AttributeError:
            return cached

        for item in items:
            if isinstance(item, UserResponse):
                continue
            if isinstance(item, dict):
                if not item.get("updated_at"):
                    item["updated_at"] = item.get("created_at") or datetime.now().isoformat()
                if not item.get("created_at"):
                    item["created_at"] = item.get("updated_at") or datetime.now().isoformat()

        return cached

    def _generate_cache_key(
        self, page, size, is_active=None, is_admin=None, sort_by=None, sort_order=None
    ):
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
                is_admin=request.is_admin,
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

    async def get_user_by_public_id(self, public_id):
        """通过 public_id 获取用户（支持 public_id 和内部 id）"""
        # 尝试作为整数（内部ID）
        try:
            internal_id = int(public_id)
            return await User.get_or_none(id=internal_id)
        except (ValueError, TypeError):
            pass
        # 作为 public_id 查询
        return await User.get_or_none(public_id=public_id)

    async def get_user_by_username(self, username):
        return await User.get_or_none(username=username)

    async def get_users_by_ids(self, user_ids):
        if not user_ids:
            return []
        return await User.filter(id__in=user_ids).all()

    async def is_admin(self, user_id):
        """检查用户是否为管理员（带缓存）"""
        cache_key = f"user:admin:{user_id}"
        try:
            from antcode_core.infrastructure.redis import redis_pool

            cached = await redis_pool.get(cache_key)
            if cached is not None:
                return cached == "1"
        except Exception:
            pass

        user = await User.get_or_none(id=user_id).only("id", "is_admin")
        is_admin = bool(user and user.is_admin)

        try:
            from antcode_core.infrastructure.redis import redis_pool

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
        page=1,
        size=20,
        is_active=None,
        is_admin=None,
        sort_by=None,
        sort_order=None,
    ):
        """获取用户列表（带缓存）"""
        cache_key = self._generate_cache_key(page, size, is_active, is_admin, sort_by, sort_order)

        # 尝试缓存
        try:
            if cached := await user_cache.get(cache_key):
                return self._normalize_cached_user_list(cached)
        except Exception:
            pass

        # 构建查询
        query = User.all()
        if is_active is not None:
            query = query.filter(is_active=is_active)
        if is_admin is not None:
            query = query.filter(is_admin=is_admin)

        total = await query.count()

        # 排序
        order_field = "-created_at"
        if sort_by in self.ALLOWED_SORT_FIELDS:
            prefix = "-" if str(sort_order or "desc").lower() == "desc" else ""
            order_field = f"{prefix}{sort_by}"

        users = await query.order_by(order_field).offset((page - 1) * size).limit(size)

        # 构建响应
        user_list = []
        for u in users:
            created_at = u.created_at or datetime.now()
            updated_at = u.updated_at or created_at
            user_list.append(
                UserResponse(
                    id=u.public_id,
                    username=u.username,
                    email=u.email,
                    is_active=u.is_active,
                    is_admin=u.is_admin,
                    created_at=created_at,
                    updated_at=updated_at,
                    last_login_at=u.last_login_at,
                    is_online=self._is_user_online(u.last_login_at),
                )
            )

        pages = (total + size - 1) // size
        result = {
            "data": {
                "items": user_list,
                "page": page,
                "size": size,
                "total": total,
                "pages": pages,
            },
            "pagination": PaginationInfo(page=page, size=size, total=total, pages=pages),
        }

        with contextlib.suppress(Exception):
            await user_cache.set(cache_key, result)

        return result

    async def get_simple_user_list(self):
        """获取简易用户列表（带缓存）"""
        cache_key = "user:list:simple"

        try:
            if cached := await user_cache.get(cache_key):
                return cached
        except Exception:
            pass

        users = await User.all().order_by("username")
        result = [
            UserSimpleResponse(id=u.public_id, username=u.username).model_dump() for u in users
        ]

        with contextlib.suppress(Exception):
            await user_cache.set(cache_key, result)

        return result

    async def update_user(self, user_id, request):
        """更新用户（支持 public_id 和内部 id）"""
        user = await self.get_user_by_public_id(user_id)
        if not user:
            raise ValueError("用户不存在")

        update_data = request.model_dump(exclude_unset=True)

        new_username = update_data.get("username")
        if new_username and new_username != user.username:
            existing_user = await User.get_or_none(username=new_username)
            if existing_user and existing_user.id != user.id:
                raise IntegrityError("用户名已存在")

        new_email = update_data.get("email")
        if new_email and new_email != user.email:
            existing_email = await User.get_or_none(email=new_email)
            if existing_email and existing_email.id != user.id:
                raise IntegrityError("邮箱已存在")

        for field, value in update_data.items():
            if field in {"old_password", "new_password"}:
                continue
            setattr(user, field, value)

        old_password = update_data.get("old_password")
        new_password = update_data.get("new_password")
        if new_password:
            if old_password and not user.verify_password(old_password):
                raise ValueError("当前密码错误")

            is_valid, error_msg = validate_password_strength(new_password)
            if not is_valid:
                raise ValueError(error_msg)

            user.set_password(new_password)

        await user.save()
        await self._invalidate_user_cache(user.id)

        logger.info(f"用户已更新: {user.username}")
        return user

    async def update_user_password(self, user_id, request, current_user_id):
        """更新用户密码（支持 public_id 和内部 id）"""
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
        """重置用户密码（支持 public_id 和内部 id）"""
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
        """删除用户（支持 public_id 和内部 id，级联删除关联数据）"""
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

    async def _cascade_delete_user_data(self, user_id):
        """级联删除用户的所有关联数据"""
        from antcode_core.domain.models import UserWorkerPermission

        deleted = {
            "worker_permissions": 0,
        }

        try:
            # 删除用户 Worker 权限
            deleted["worker_permissions"] = await UserWorkerPermission.filter(user_id=user_id).delete()
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
