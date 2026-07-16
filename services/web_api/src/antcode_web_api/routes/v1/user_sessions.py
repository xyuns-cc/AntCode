"""Administrative user-session revocation routes."""

from antcode_core.application.services.audit import audit_service
from antcode_core.application.services.users.user_service import user_service
from antcode_core.common.security.auth import TokenData, get_current_super_admin
from antcode_core.domain.models import UserRole
from antcode_core.domain.models.audit_log import AuditAction
from fastapi import APIRouter, Depends, HTTPException, Request, status

from antcode_web_api.response import success

router = APIRouter()


def _role_value(user) -> str:
    return user.role.value if hasattr(user.role, "value") else str(user.role)


@router.post(
    "/{user_id}/kick",
    summary="强制用户下线",
    description="撤销目标用户的全部服务端会话，仅超级管理员可用。",
)
async def revoke_user_sessions(
    user_id: str,
    request: Request,
    current_admin: TokenData = Depends(get_current_super_admin),
):
    target = await user_service.get_user_by_public_id(user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if target.id == current_admin.user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能强制自己下线")
    if _role_value(target) == UserRole.SUPER_ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="不能强制超级管理员下线")

    admin = await user_service.get_user_by_id(current_admin.user_id)
    if admin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="当前管理员不存在")

    revoked = await user_service.revoke_all_sessions(target.id)
    await audit_service.log_user_action(
        action=AuditAction.LOGOUT,
        operator_username=admin.username,
        target_user_id=target.id,
        target_username=target.username,
        operator_id=admin.id,
        ip_address=request.client.host if request.client else None,
        new_value={"revoked_sessions": revoked},
        description=f"强制用户下线: {target.username}",
    )
    return success({"revoked_sessions": revoked}, message=f"已撤销 {revoked} 个活跃会话")


__all__ = ["router"]
