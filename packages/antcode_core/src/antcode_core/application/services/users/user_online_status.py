"""在线判定的唯一真源：``user_sessions``。

此前的判定是"最近 15 分钟登录过"（``last_login_at`` 窗口），与会话状态无关，
两个方向都会给错答案：靠 refresh 续期的活跃用户被判离线；刚被「踢下线」
（``revoke_all_sessions`` 写 ``revoked_at``）的用户在窗口内仍显示在线，管理员
无法确认是否踢掉。这里改判"仍持有未撤销、未过期的会话"——``revoked_at`` 正是
踢下线自己写的字段，判定与操作用的是同一份数据。

会话态不进用户列表缓存：缓存命中也要重查一次，否则踢完人刷新还是旧结论。
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, cast

from antcode_core.domain.models.user import User
from antcode_core.domain.models.user_session import UserSession


async def active_session_user_ids(user_ids: Iterable[int]) -> set[int]:
    """返回这些用户里仍持有有效会话的内部 id。"""
    wanted = [int(user_id) for user_id in user_ids]
    if not wanted:
        return set()
    rows = await UserSession.filter(
        user_id__in=wanted,
        revoked_at__isnull=True,
        expires_at__gt=datetime.now(UTC),
    ).values_list("user_id", flat=True)
    return {int(cast(int, row)) for row in rows}


async def is_user_online(user_id: int) -> bool:
    """单个用户是否仍有有效会话。"""
    return user_id in await active_session_user_ids([user_id])


async def online_public_ids(public_ids: Iterable[str]) -> set[str]:
    """把一批 ``public_id`` 映射成"仍有有效会话"的子集。"""
    wanted = [public_id for public_id in public_ids if public_id]
    if not wanted:
        return set()
    rows = await User.filter(public_id__in=wanted).values_list("id", "public_id")
    public_by_internal = {int(internal_id): str(public_id) for internal_id, public_id in rows}
    online = await active_session_user_ids(public_by_internal)
    return {public_by_internal[internal_id] for internal_id in online if internal_id in public_by_internal}


def _public_id(item: Any) -> str:
    """列表项可能是 ``UserResponse``（新查）或 dict（缓存反序列化后）。"""
    if isinstance(item, dict):
        return str(item.get("id") or "")
    return str(getattr(item, "id", "") or "")


def _with_online(item: Any, online: bool) -> Any:
    if isinstance(item, dict):
        return {**item, "is_online": online}
    return item.model_copy(update={"is_online": online})


async def apply_online_status(result: dict) -> dict:
    """用真实会话状态重建列表项的 ``is_online``，不改动入参。"""
    items = list(result["data"]["items"])
    online = await online_public_ids(_public_id(item) for item in items)
    refreshed = [_with_online(item, _public_id(item) in online) for item in items]
    return {**result, "data": {**result["data"], "items": refreshed}}


__all__ = [
    "active_session_user_ids",
    "apply_online_status",
    "is_user_online",
    "online_public_ids",
]
