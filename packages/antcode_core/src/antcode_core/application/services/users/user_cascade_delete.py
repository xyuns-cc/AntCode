"""Transactional cleanup for physical User deletion."""

from tortoise.transactions import in_transaction

from antcode_core.application.services.users.user_ownership_lock import lock_user
from antcode_core.domain.models import (
    GitCredential,
    GitRepository,
    UserSession,
    UserWorkerPermission,
)


async def cascade_delete_user_data(user) -> dict[str, int]:
    """Delete user-owned rows and the User atomically."""
    deleted = {
        "worker_permissions": 0,
        "git_repositories": 0,
        "git_credentials": 0,
        "user_sessions": 0,
    }
    async with in_transaction() as connection:
        locked_user = await lock_user(connection, user.id, active_only=False)
        deleted["worker_permissions"] = await UserWorkerPermission.filter(user_id=user.id).using_db(connection).delete()
        deleted["git_repositories"] = await GitRepository.filter(owner_user_id=user.id).using_db(connection).delete()
        deleted["git_credentials"] = await GitCredential.filter(owner_user_id=user.id).using_db(connection).delete()
        deleted["user_sessions"] = await UserSession.filter(user_id=user.id).using_db(connection).delete()
        await locked_user.delete(using_db=connection)
    return deleted


__all__ = ["cascade_delete_user_data"]
