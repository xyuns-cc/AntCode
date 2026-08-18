"""用户响应里的「在线」必须取自会话表，而不是 last_login_at 窗口。

这条守的是路由层的取数来源：``_build_user_response`` 只要退回"最近 15 分钟
登录过"的判定，下面两条就会同时变红——刚被踢下线的用户 last_login_at 就是
此刻，正好落在旧窗口内。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from antcode_core.domain.models import User, UserSession
from antcode_core.domain.models.user import UserRole
from antcode_web_api.routes.v1 import users as user_routes
from tortoise import Tortoise

KICKED_USER_ID = 51
ACTIVE_USER_ID = 52
SESSION_TTL_DAYS = 7
STALE_LOGIN_HOURS = 4


@pytest_asyncio.fixture
async def user_tables():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


async def _user_with_session(user_id: int, *, last_login_at: datetime, revoked: bool) -> User:
    user = await User.create(
        id=user_id,
        username=f"user-{user_id}",
        password_hash="x",
        role=UserRole.USER,
        last_login_at=last_login_at,
    )
    now = datetime.now(UTC)
    await UserSession.create(
        user_id=user_id,
        jti=f"jti-{user_id}",
        expires_at=now + timedelta(days=SESSION_TTL_DAYS),
        revoked_at=now if revoked else None,
    )
    return user


@pytest.mark.asyncio
async def test_kicked_user_is_offline_despite_fresh_last_login(user_tables) -> None:
    user = await _user_with_session(KICKED_USER_ID, last_login_at=datetime.now(UTC), revoked=True)

    response = await user_routes._build_user_response(user)

    assert response.is_online is False


@pytest.mark.asyncio
async def test_refresh_kept_user_is_online_despite_stale_last_login(user_tables) -> None:
    stale = datetime.now(UTC) - timedelta(hours=STALE_LOGIN_HOURS)
    user = await _user_with_session(ACTIVE_USER_ID, last_login_at=stale, revoked=False)

    response = await user_routes._build_user_response(user)

    assert response.is_online is True
