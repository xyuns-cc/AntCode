"""Database row lock shared by User deletion and owned-resource creation."""

from antcode_core.domain.models.user import User


async def lock_user(connection, user_id: int, *, active_only: bool) -> User:
    filters: dict[str, object] = {"id": user_id}
    if active_only:
        filters["is_active"] = True
    user = await User.filter(**filters).using_db(connection).select_for_update().only("id", "is_active").first()
    if user is None:
        raise ValueError("用户不存在或已停用")
    return user


__all__ = ["lock_user"]
