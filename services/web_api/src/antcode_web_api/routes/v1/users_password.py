"""改密与重置密码接口。

从 ``users.py`` 拆出来，是因为这三条路由共享同一件事：口令的传输形态。
登录早就走 ``/auth/public-key`` 的 RSA-OAEP-256 公钥加密，改密与重置却一直
收明文 JSON——同一类机密两个端点两种待遇。三条路由现在共用
``resolve_transmitted_password``，策略与登录同源。
"""

from antcode_core.application.services.users.user_service import user_service
from antcode_core.common.security.auth import (
    TokenData,
    get_current_super_admin,
    get_current_user,
)
from antcode_core.common.security.login_crypto import (
    LoginPasswordCryptoError,
    resolve_transmitted_password,
)
from antcode_core.domain.schemas import (
    BaseResponse,
    UserAdminPasswordUpdateRequest,
    UserPasswordUpdateRequest,
)
from antcode_core.domain.schemas.user import PasswordEnvelopeFields
from fastapi import APIRouter, Depends, HTTPException, Request, status

from antcode_web_api.response import success
from antcode_web_api.routes.v1.mutation_audit import audit_password_changed
from antcode_web_api.routing import promote_static_routes

router = APIRouter()


def _decrypt(encrypted: str | None, plaintext: str | None, envelope: PasswordEnvelopeFields) -> str:
    """把一个口令字段收敛成明文；密文格式/策略错误一律 400。

    与登录同一个异常类型和同一个状态码，避免两个端点对同一种错误给出两套说辞。
    """
    try:
        return resolve_transmitted_password(
            plaintext=plaintext,
            encrypted=encrypted,
            algorithm=envelope.encryption,
            key_id=envelope.key_id,
        )
    except LoginPasswordCryptoError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _resolved(request: UserPasswordUpdateRequest) -> UserPasswordUpdateRequest:
    """返回明文已就位的新请求对象。

    用 ``model_copy`` 而不是就地赋值：入参不可变，且此处刻意不重跑校验——
    ``new_password`` 的长度下限是给明文提交用的，解密后的强度校验由
    ``validate_password_strength`` 在 service 层统一执行，两处都做会给出
    两套不一致的口径。
    """
    return request.model_copy(
        update={
            "old_password": _decrypt(request.encrypted_old_password, request.old_password, request),
            "new_password": _decrypt(request.encrypted_new_password, request.new_password, request),
        }
    )


@router.put(
    "/{user_id}/password",
    response_model=BaseResponse,
    summary="修改用户密码（需当前密码，仅本人）",
    tags=["用户管理"],
)
async def update_user_password(
    user_id: str,
    request: UserPasswordUpdateRequest,
    current_user: TokenData = Depends(get_current_user),
    *,
    http_request: Request,
):
    """修改用户密码。

    仅允许用户改自己的密码并强制校验当前密码；管理员重置他人密码走
    ``PUT /users/{id}/reset-password``（需 super_admin），避免提权路径。
    """
    target_user = await user_service.get_user_by_public_id(user_id)
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if target_user.id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅可修改自己的密码；如需重置他人密码请使用 reset-password 接口",
        )
    try:
        await user_service.update_user_password(user_id, _resolved(request), current_user.user_id)
        await audit_password_changed(http_request, current_user, target=target_user, reset_by_admin=False)
        return success(None, message="密码修改成功")
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/change-password",
    response_model=BaseResponse,
    summary="修改当前用户密码",
    tags=["用户管理"],
)
async def change_password(
    request: UserPasswordUpdateRequest,
    current_user: TokenData = Depends(get_current_user),
    *,
    http_request: Request,
):
    """修改当前用户密码"""
    try:
        target = await user_service.update_user_password(current_user.user_id, _resolved(request), current_user.user_id)
        await audit_password_changed(http_request, current_user, target=target, reset_by_admin=False)
        return success(None, message="密码修改成功")
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put(
    "/{user_id}/reset-password",
    response_model=BaseResponse,
    summary="重置用户密码",
    tags=["用户管理"],
)
async def reset_user_password(
    user_id: str,
    request: UserAdminPasswordUpdateRequest,
    current_admin: TokenData = Depends(get_current_super_admin),
    *,
    http_request: Request,
):
    """重置用户密码（仅超级管理员）"""
    new_password = _decrypt(request.encrypted_new_password, request.new_password, request)
    try:
        target = await user_service.reset_user_password(user_id, new_password)
    except ValueError as exc:
        status_code = status.HTTP_404_NOT_FOUND if str(exc) == "用户不存在" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    await audit_password_changed(http_request, current_admin, target=target, reset_by_admin=True)
    return success(None, message="密码重置成功")


promote_static_routes(router)
