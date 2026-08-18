"""「在线」判定必须来自 user_sessions，而不是 last_login_at 窗口。

旧用例只喂 ``UserService._is_user_online`` 一个 datetime，判的是"最近 15 分钟
登录过"——那正是走查里两个反例的成因：靠 refresh 续期的活跃用户被判离线，
刚被踢下线的用户在窗口内仍显示在线。所以这里不再测那个纯函数，改成真表真查：
建真实 User + UserSession，覆盖 revoked / 过期 / 有效三种会话状态。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from antcode_core.application.services.users import user_online_status
from antcode_core.application.services.users.user_service import user_service
from antcode_core.domain.models import User, UserSession
from antcode_core.domain.models.user import UserRole
from antcode_core.domain.schemas.user import UserResponse
from tortoise import Tortoise

ACTIVE_USER_ID = 41
KICKED_USER_ID = 42
EXPIRED_USER_ID = 43
SESSION_TTL_DAYS = 7
STALE_LOGIN_HOURS = 4


@pytest_asyncio.fixture
async def session_tables():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


async def _create_user(user_id: int, *, last_login_at: datetime | None) -> User:
    return await User.create(
        id=user_id,
        username=f"user-{user_id}",
        password_hash="x",
        role=UserRole.USER,
        last_login_at=last_login_at,
    )


async def _create_session(user_id: int, *, revoked: bool, expired: bool) -> None:
    now = datetime.now(UTC)
    await UserSession.create(
        user_id=user_id,
        jti=f"jti-{user_id}-{int(revoked)}{int(expired)}",
        expires_at=now - timedelta(days=1) if expired else now + timedelta(days=SESSION_TTL_DAYS),
        revoked_at=now if revoked else None,
    )


@pytest.mark.asyncio
async def test_active_session_is_online_even_when_last_login_is_hours_old(session_tables) -> None:
    """走查反例 1：admin 靠 refresh 续期，last_login_at 是 4 小时前但会话有效。"""
    stale_login = datetime.now(UTC) - timedelta(hours=STALE_LOGIN_HOURS)
    await _create_user(ACTIVE_USER_ID, last_login_at=stale_login)
    await _create_session(ACTIVE_USER_ID, revoked=False, expired=False)

    assert await user_online_status.is_user_online(ACTIVE_USER_ID) is True


@pytest.mark.asyncio
async def test_revoked_session_is_offline_even_when_just_logged_in(session_tables) -> None:
    """走查反例 2：刚被「踢下线」的用户 last_login_at 仍在 15 分钟窗口内。"""
    await _create_user(KICKED_USER_ID, last_login_at=datetime.now(UTC))
    await _create_session(KICKED_USER_ID, revoked=True, expired=False)

    assert await user_online_status.is_user_online(KICKED_USER_ID) is False


@pytest.mark.asyncio
async def test_expired_session_is_offline(session_tables) -> None:
    await _create_user(EXPIRED_USER_ID, last_login_at=datetime.now(UTC))
    await _create_session(EXPIRED_USER_ID, revoked=False, expired=True)

    assert await user_online_status.is_user_online(EXPIRED_USER_ID) is False


@pytest.mark.asyncio
async def test_user_without_any_session_is_offline(session_tables) -> None:
    await _create_user(ACTIVE_USER_ID, last_login_at=datetime.now(UTC))

    assert await user_online_status.is_user_online(ACTIVE_USER_ID) is False


@pytest.mark.asyncio
async def test_apply_online_status_rebuilds_cached_list_items(session_tables) -> None:
    """缓存命中路径也必须现算：踢完人刷新不能还显示在线。"""
    active = await _create_user(ACTIVE_USER_ID, last_login_at=datetime.now(UTC))
    kicked = await _create_user(KICKED_USER_ID, last_login_at=datetime.now(UTC))
    await _create_session(ACTIVE_USER_ID, revoked=False, expired=False)
    await _create_session(KICKED_USER_ID, revoked=True, expired=False)
    # 一条是缓存反序列化后的 dict，一条是新查出来的 UserResponse，两条路都要覆盖。
    cached_item = {"id": active.public_id, "username": active.username, "is_online": False}
    fresh_item = UserResponse(
        id=kicked.public_id,
        username=kicked.username,
        is_active=True,
        is_admin=False,
        created_at=kicked.created_at,
        updated_at=kicked.updated_at,
        is_online=True,
    )
    payload = {"data": {"items": [cached_item, fresh_item]}, "pagination": {}}

    refreshed = await user_online_status.apply_online_status(payload)

    assert refreshed["data"]["items"][0]["is_online"] is True
    assert refreshed["data"]["items"][1].is_online is False
    # 入参不被就地修改
    assert cached_item["is_online"] is False
    assert fresh_item.is_online is True


@pytest.mark.asyncio
async def test_user_list_never_serves_online_state_from_cache(session_tables, monkeypatch) -> None:
    """列表缓存里带着"在线"的旧快照，也必须被真实会话状态覆盖回来。

    这是走查那条"踢完人刷新还是在线"的另一半成因：``is_online`` 一旦跟着
    用户列表进 Redis，就会在 TTL 内持续给出过期结论。
    """
    kicked = await _create_user(KICKED_USER_ID, last_login_at=datetime.now(UTC))
    await _create_session(KICKED_USER_ID, revoked=True, expired=False)
    stale_cache = {
        "data": {
            "items": [{"id": kicked.public_id, "username": kicked.username, "is_online": True}],
            "page": 1,
            "size": 20,
            "total": 1,
            "pages": 1,
        },
        "pagination": {"page": 1, "size": 20, "total": 1, "pages": 1},
    }
    monkeypatch.setattr(user_service, "_generate_cache_key", lambda **_kwargs: "user:list:test")

    async def cache_hit(_key):
        return stale_cache

    monkeypatch.setattr("antcode_core.infrastructure.cache.user_cache.get", cache_hit)

    result = await user_service.get_users_list(page=1, size=20)

    assert result["data"]["items"][0]["is_online"] is False


@pytest.mark.asyncio
async def test_user_list_reports_active_session_as_online(session_tables, monkeypatch) -> None:
    stale_login = datetime.now(UTC) - timedelta(hours=STALE_LOGIN_HOURS)
    await _create_user(ACTIVE_USER_ID, last_login_at=stale_login)
    await _create_session(ACTIVE_USER_ID, revoked=False, expired=False)

    async def cache_miss(_key):
        return None

    async def cache_write(_key, _value, ttl=None):
        return True

    monkeypatch.setattr("antcode_core.infrastructure.cache.user_cache.get", cache_miss)
    monkeypatch.setattr("antcode_core.infrastructure.cache.user_cache.set", cache_write)

    result = await user_service.get_users_list(page=1, size=20)

    assert [item.is_online for item in result["data"]["items"]] == [True]
