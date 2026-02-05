"""用户会话服务"""

from datetime import datetime, timedelta, timezone

from loguru import logger

from src.models.user_session import UserSession


class UserSessionService:
    """用户会话服务

    设计原则：
    - web 会话：每次登录创建新的会话，可被踢下线撤销
    - node/service 会话：尽量复用，避免会话表膨胀
    """

    WEB_ONLINE_WINDOW_SECONDS = 300  # 5分钟内有活动视为在线
    LAST_SEEN_UPDATE_INTERVAL_SECONDS = 30  # 限制 last_seen 写频率

    async def create_web_session(self, user_id, ip_address=None, user_agent=None):
        return await UserSession.create(
            user_id=user_id,
            session_type="web",
            ip_address=ip_address,
            user_agent=user_agent,
            last_seen_at=datetime.now(timezone.utc),
        )

    async def get_or_create_service_session(self, user_id):
        session = await UserSession.filter(
            user_id=user_id,
            session_type="service",
            revoked_at__isnull=True,
        ).first()
        if session:
            return session

        return await UserSession.create(
            user_id=user_id,
            session_type="service",
            last_seen_at=datetime.now(timezone.utc),
        )

    async def get_or_create_node_session(self, user_id, node_id):
        session = await UserSession.filter(
            user_id=user_id,
            session_type="node",
            node_id=node_id,
            revoked_at__isnull=True,
        ).first()
        if session:
            return session

        return await UserSession.create(
            user_id=user_id,
            session_type="node",
            node_id=node_id,
            last_seen_at=datetime.now(timezone.utc),
        )

    async def get_session_by_public_id(self, session_public_id: str):
        if not session_public_id:
            return None
        return await UserSession.get_or_none(public_id=session_public_id)

    async def touch_session(self, session):
        if not session:
            return

        now = datetime.now(timezone.utc)
        last_seen_at = session.last_seen_at

        if last_seen_at and (now - last_seen_at).total_seconds() < self.LAST_SEEN_UPDATE_INTERVAL_SECONDS:
            return

        try:
            await UserSession.filter(id=session.id).update(last_seen_at=now)
        except Exception as e:
            logger.debug(f"更新会话 last_seen 失败: {e}")

    async def revoke_session(self, session_public_id: str):
        if not session_public_id:
            return 0
        now = datetime.now(timezone.utc)
        return await UserSession.filter(public_id=session_public_id, revoked_at__isnull=True).update(revoked_at=now)

    async def revoke_user_web_sessions(self, user_id):
        now = datetime.now(timezone.utc)
        return await UserSession.filter(
            user_id=user_id,
            session_type="web",
            revoked_at__isnull=True,
        ).update(revoked_at=now)

    async def get_online_user_ids(self, user_ids=None):
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.WEB_ONLINE_WINDOW_SECONDS)

        query = UserSession.filter(
            session_type="web",
            revoked_at__isnull=True,
            last_seen_at__gte=cutoff,
        )
        if user_ids:
            query = query.filter(user_id__in=user_ids)

        rows = await query.distinct().values_list("user_id", flat=True)
        return set(rows or [])


user_session_service = UserSessionService()
