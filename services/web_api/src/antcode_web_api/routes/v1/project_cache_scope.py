"""Authorization-bound cache scopes for project responses."""

from typing import Any


def project_authorization_cache_scope(_args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    current_user = kwargs["current_user"]
    return f"user:{current_user.user_id}:role:{current_user.role}:admin:{int(current_user.is_admin)}"


__all__ = ["project_authorization_cache_scope"]
