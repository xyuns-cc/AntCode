"""用户服务"""

import re
from datetime import UTC, datetime

from loguru import logger
from tortoise.exceptions import IntegrityError
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from antcode_core.application.services.users.user_cascade_delete import (
    cascade_delete_user_data,
)
from antcode_core.application.services.users.user_online_status import apply_online_status
from antcode_core.common.hash_utils import calculate_content_hash
from antcode_core.domain.models.user import User, consume_dummy_password_check, password_fits_bcrypt
from antcode_core.domain.schemas.common import PaginationInfo
from antcode_core.domain.schemas.user import UserResponse, UserSimpleResponse
from antcode_core.infrastructure.cache import user_cache


def validate_password_strength(password):
    """验证密码长度、bcrypt 字节预算和字符组成。"""
    too_short = len(password) < 8
    if too_short or not password_fits_bcrypt(password):
        message = "密码长度至少为 8 个字符" if too_short else "密码 UTF-8 编码不能超过 72 字节"
        return False, message

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
        self,
        *,
        page,
        size,
        search=None,
        is_active=None,
        is_admin=None,
        sort_by=None,
        sort_order=None,
    ):
        """生成缓存键"""
        parts = [f"p{page}", f"s{size}"]
        if search:
            parts.append(f"search{search}")
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
                consume_dummy_password_check(password)
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
            raise

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
            if hasattr(request, "role") and request.role:
                from antcode_core.domain.models.user import UserRole

                try:
                    user.role = UserRole(request.role)
                except ValueError:
                    pass

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

        cached = await user_cache.get(cache_key)
        if cached is not None:
            return cached == "1"

        user = await User.get_or_none(id=user_id).only("id", "is_admin")
        is_admin = bool(user and user.is_admin)

        await user_cache.set(cache_key, "1" if is_admin else "0", ttl=300)

        return is_admin

    async def update_last_login(self, user_id):
        await User.filter(id=user_id).update(last_login_at=datetime.now())
        logger.debug(f"更新最后登录时间: user_id={user_id}")

    async def get_users_list(
        self,
        *,
        page=1,
        size=20,
        search=None,
        is_active=None,
        is_admin=None,
        sort_by=None,
        sort_order=None,
    ):
        """获取用户列表（带缓存）。

        ``is_online`` 一律在缓存之外由 ``apply_online_status`` 现算：它是会话态，
        缓存进去就会出现"人已踢下线、刷新仍显示在线"。
        """
        normalized_search = search.strip() if search else None
        cache_key = self._generate_cache_key(
            page=page,
            size=size,
            search=normalized_search,
            is_active=is_active,
            is_admin=is_admin,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        if cached := await user_cache.get(cache_key):
            return await apply_online_status(self._normalize_cached_user_list(cached))

        # 构建查询
        query = User.all()
        if normalized_search:
            query = query.filter(Q(public_id__icontains=normalized_search) | Q(username__icontains=normalized_search))
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
                    role=u.role.value if hasattr(u.role, "value") else str(u.role),
                    created_at=created_at,
                    updated_at=updated_at,
                    last_login_at=u.last_login_at,
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

        await user_cache.set(cache_key, result)

        return await apply_online_status(result)

    async def get_simple_user_list(self):
        """获取简易用户列表（带缓存）"""
        cache_key = "user:list:simple"

        if cached := await user_cache.get(cache_key):
            return cached

        users = await User.all().order_by("username")
        result = [UserSimpleResponse(id=u.public_id, username=u.username).model_dump() for u in users]

        await user_cache.set(cache_key, result)

        return result

    # 通用更新接口允许写入的字段白名单——权限相关的 role/is_admin 以及
    # 凭证相关的 password 必须走各自的独立端点，绝不允许从这里透传。
    _UPDATABLE_FIELDS: frozenset[str] = frozenset({"username", "email", "is_active"})

    async def update_user(self, user_id, request):
        """更新用户（支持 public_id 和内部 id）。

        安全约束：只处理 ``_UPDATABLE_FIELDS`` 白名单里的字段。即便调用方
        (或未来某个 schema 演进) 把 ``role``/``is_admin``/``new_password``
        塞进 payload，也会在这里被静默丢弃 —— 单点防线，避免 setattr 走漏。
        """
        user = await self.get_user_by_public_id(user_id)
        if not user:
            raise ValueError("用户不存在")

        raw_data = request.model_dump(exclude_unset=True)
        # 白名单过滤：任何未在白名单里的字段（含 role/is_admin/old_password/
        # new_password 等敏感字段）都拒绝写入。
        update_data = {k: v for k, v in raw_data.items() if k in self._UPDATABLE_FIELDS}

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
            setattr(user, field, value)

        await user.save()
        await self._invalidate_user_cache(user.id)

        logger.info(f"用户已更新: {user.username}")
        return user

    async def update_user_password(self, user_id, request, current_user_id) -> User:
        """更新用户密码（支持 public_id 和内部 id）；返回被改密的用户供审计取名。"""
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
        # 改密即撤销全部会话：旧 refresh token 立刻失效，用户列表的"在线"
        # 也随之翻成离线（在线判定读的正是 user_sessions.revoked_at）。
        await self.revoke_all_sessions(user.id)

        logger.info(f"密码已更新: {user.username}")
        return user

    async def reset_user_password(self, user_id, new_password) -> User:
        """重置用户密码（支持 public_id 和内部 id）；返回被重置的用户供审计取名。"""
        user = await self.get_user_by_public_id(user_id)
        if not user:
            raise ValueError("用户不存在")

        # 验证新密码强度
        is_valid, error_msg = validate_password_strength(new_password)
        if not is_valid:
            raise ValueError(error_msg)

        user.set_password(new_password)
        await user.save()
        # 同 update_user_password：重置后旧会话全部作废，在线状态随之翻转。
        await self.revoke_all_sessions(user.id)

        logger.info(f"密码已重置: {user.username}")
        return user

    async def delete_user(self, user_id, current_user_id):
        """删除用户（支持 public_id 和内部 id，级联删除关联数据）

        P1-20 修复要点:
        - 事务化:权限/凭证清理 + user.delete() 走同一 `in_transaction()`,
          任一步骤失败整体回滚,避免出现"权限清了、user 还在"的半状态。
        - **拒绝策略**:名下仍有资源(user_id 引用)就拒绝删除并提示先移交。不选
          "转移到系统用户"是因为:1) 项目含 Git 凭证等敏感上下文,静默改归属会断
          审计链;2) 代码库没有 SYSTEM_USER 哨兵概念,强行引入等于新增隐式全局对
          象;3) 与已有"不能删除自己"的显式拒绝分支一致。需要"强制清理"时,业务
          侧应先调用 delete_project_cascade 逐个处理掉项目,再来删用户。
        """
        user = await self.get_user_by_public_id(user_id)
        if not user:
            raise ValueError("用户不存在")

        if user.id == current_user_id:
            raise ValueError("不能删除自己")

        current_user = await User.get(id=current_user_id)
        if not current_user.is_admin:
            raise PermissionError("仅管理员可删除用户")

        # 拒绝策略:名下仍有项目/任务/爬取批次时不允许删,要求先移交或删除资源。
        # 批次必须单独查:管理员可在他人项目下建批次(crawl.py::_verify_project_access
        # 放行管理员),CrawlBatch.user_id 因此可以 != Project.user_id;只查项目和任务
        # 会放过"只拥有批次"的用户,批次留下查不到人的悬空 user_id。
        from antcode_core.domain.models import CrawlBatch, Project
        from antcode_core.domain.models.task import Task

        project_count = await Project.filter(user_id=user.id).count()
        if project_count > 0:
            raise ValueError(f"该用户名下仍有 {project_count} 个项目,请先移交或删除后再删除用户")
        task_count = await Task.filter(user_id=user.id).count()
        if task_count > 0:
            raise ValueError(f"该用户名下仍有 {task_count} 个任务,请先移交或删除后再删除用户")
        batch_count = await CrawlBatch.filter(user_id=user.id).count()
        if batch_count > 0:
            raise ValueError(f"该用户名下仍有 {batch_count} 个爬取批次,请先移交或删除后再删除用户")

        # 级联删除用户关联数据(单事务原子)
        deleted_counts = await self._cascade_delete_user_data(user)

        await self._invalidate_user_cache(user.id)

        logger.info(f"用户已删除: {user.username}, 级联删除: {deleted_counts}")

    async def revoke_all_sessions(self, user_id: int) -> int:
        """P1-09: 一键撤销该用户所有活跃 refresh session。

        触发场景: 改密、离职、检测到异常登录、admin 强制登出。实现: 把
        ``UserSession.revoked_at`` 从 NULL 改为 now(); 已 revoke 的记录不动
        (幂等), 避免二次撤销把审计时间戳覆盖成新值。返回被撤销的 session 数。
        """
        from datetime import datetime

        from antcode_core.domain.models.user_session import UserSession

        now = datetime.now(UTC)
        updated = await UserSession.filter(user_id=user_id, revoked_at__isnull=True).update(revoked_at=now)
        logger.info(f"P1-09: revoke_all_sessions user={user_id} revoked_count={updated}")
        return updated

    async def delete_user_with_reassign(
        self,
        user_id: int,
        reassign_to_user_id: int | None = None,
    ) -> bool:
        """P1-09: 软删除用户, 可选把关联资源转移给接收人。

        为什么不物理删: user_id 是 BigIntField 裸引用, 没有 ON DELETE 约束, 物理删
        会留 dangling 引用 (前端 join 空导致 500); 且审计链路要求保留原始 user_id。

        流程: 1) 若指定 ``reassign_to_user_id``, 把 Task / Project / CrawlBatch
        的 ``user_id`` 批量改成接收人 (离职员工资产转 admin 池); 2) 撤销其所有
        refresh session; 3) ``is_active=False`` + username 后缀化完成软删除。

        与 ``delete_user`` 的区别: 后者走 "有资源就拒绝" 严格路径, 需业务侧显式清
        理; 本方法是 "带转移的强制下线", 用于离职流程等批处理场景。

        返回 True = 已处理; False = 用户不存在。
        """
        from datetime import datetime

        from antcode_core.domain.models import CrawlBatch, Project, User
        from antcode_core.domain.models.task import Task
        from antcode_core.domain.models.user_session import UserSession

        user = await User.get_or_none(id=user_id)
        if user is None:
            return False

        async with in_transaction():
            reassigned_counts: dict[str, int] = {}
            if reassign_to_user_id:
                # 校验接收人存在且 active, 避免把资产转给一个僵尸账号
                receiver = await User.get_or_none(id=reassign_to_user_id)
                if receiver is None or not receiver.is_active:
                    raise ValueError(f"接收人 user_id={reassign_to_user_id} 不存在或已停用")
                reassigned_counts["tasks"] = await Task.filter(user_id=user_id).update(user_id=reassign_to_user_id)
                reassigned_counts["projects"] = await Project.filter(user_id=user_id).update(
                    user_id=reassign_to_user_id
                )
                # CrawlBatch.user_id 必须跟着走: 批次 run 的所有者正是从这里解析的
                # (run_ownership.resolve_run_owner_id)。不迁移则批次连同其 run 挂在已停
                # 用账号下, 接收人一律无权, 成为无主数据。
                reassigned_counts["crawl_batches"] = await CrawlBatch.filter(user_id=user_id).update(
                    user_id=reassign_to_user_id
                )
                # Worker 归属: 当前 Worker 模型没有直接 owner 字段
                # (由 UserWorkerPermission 关联表管理), 权限清理走 revoke 路径
                # 不在这里迁移, 避免语义歧义 (共享 worker 不该被独占转移)。

            # 撤销所有 refresh session (走同一事务, 迁移失败时也一起回滚)
            now = datetime.now(UTC)
            revoked = await UserSession.filter(user_id=user_id, revoked_at__isnull=True).update(revoked_at=now)

            # 软删除: 停用 + 用户名后缀化以释放原名
            ts = int(now.timestamp())
            user.is_active = False
            user.username = f"{user.username}__deleted_{ts}"
            await user.save()

        await self._invalidate_user_cache(user_id)

        logger.info(
            f"P1-09: delete_user_with_reassign user={user_id} "
            f"reassign_to={reassign_to_user_id} reassigned={reassigned_counts if reassign_to_user_id else 'skip'} "
            f"revoked_sessions={revoked}"
        )
        return True

    async def _cascade_delete_user_data(self, user):
        """级联删除用户的关联数据(含 User 自身)。

        单个 `in_transaction()` 事务保证:权限/凭证/User 主体全部成功或
        全部回滚。审计日志(AuditLog.user_id 可空)刻意保留以维持追溯链。
        """
        try:
            return await cascade_delete_user_data(user)
        except Exception as e:
            logger.error(f"级联删除用户数据失败(事务已回滚): {e}")
            raise

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
