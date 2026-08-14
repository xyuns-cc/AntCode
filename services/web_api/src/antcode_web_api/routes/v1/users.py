"""用户管理接口"""

from datetime import datetime

from antcode_core.application.services.users.user_service import user_service
from antcode_core.common.security.auth import (
    TokenData,
    get_current_admin_user,
    get_current_super_admin,
    get_current_user,
    verify_super_admin,
)
from antcode_core.domain.models import User, UserRole, UserSession
from antcode_core.domain.schemas import (
    BaseResponse,
    PaginationResponse,
    UserAdminPasswordUpdateRequest,
    UserBatchStatusRequest,
    UserCreateRequest,
    UserListQueryParams,
    UserPasswordUpdateRequest,
    UserResponse,
    UserUpdateRequest,
)
from antcode_core.domain.schemas.common import PaginationInfo
from antcode_core.domain.schemas.user import AdminUserRoleUpdateRequest
from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from tortoise.exceptions import IntegrityError

from antcode_web_api.deps import require_role
from antcode_web_api.response import Messages, success
from antcode_web_api.response import page as page_response
from antcode_web_api.routes.v1.committed_resource_audit import (
    audit_user_created,
    audit_user_deleted,
    audit_user_role_changed,
    audit_user_updated,
)
from antcode_web_api.routing import promote_static_routes
from antcode_web_api.utils.batch_inputs import bounded_distinct_ids

router = APIRouter()


def _build_user_response(user) -> UserResponse:
    """构建用户响应"""
    is_online = False
    if user.last_login_at:
        now = datetime.now(user.last_login_at.tzinfo) if user.last_login_at.tzinfo else datetime.now()
        is_online = (now - user.last_login_at).total_seconds() <= user_service.ONLINE_WINDOW_SECONDS

    return UserResponse(
        id=user.public_id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_admin=user.is_admin,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_login_at=user.last_login_at,
        is_online=is_online,
    )


@router.get(
    "/",
    response_model=PaginationResponse,
    summary="获取用户列表",
    description="获取所有用户列表（仅管理员）",
    tags=["用户管理"],
)
async def get_users_list(
    query_params: UserListQueryParams = Depends(),
    current_admin=Depends(get_current_admin_user),
):
    """获取用户列表（仅管理员可访问，带缓存优化）"""

    try:
        result = await user_service.get_users_list(
            page=query_params.page,
            size=query_params.size,
            search=query_params.search,
            is_active=query_params.is_active,
            is_admin=query_params.is_admin,
            sort_by=query_params.sort_by,
            sort_order=query_params.sort_order,
        )

        pag = result["pagination"]
        if isinstance(pag, dict):
            pag = PaginationInfo(**pag)
        items = result["data"]["items"]
        return page_response(
            items=items,
            total=pag.total,
            page=pag.page,
            size=pag.size,
            message=Messages.QUERY_SUCCESS,
        )

    except Exception as exc:
        logger.exception("获取用户列表失败")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取用户列表失败",
        ) from exc


@router.get(
    "/simple",
    response_model=BaseResponse,
    summary="获取用户简易列表",
    description="获取所有用户的简易信息列表（仅管理员）",
    tags=["用户管理"],
)
async def get_simple_user_list(current_admin=Depends(get_current_admin_user)):
    """获取用户简易信息列表"""

    try:
        users = await user_service.get_simple_user_list()
        return success(users, message=Messages.QUERY_SUCCESS)
    except Exception as exc:
        logger.exception("获取用户简易列表失败")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取用户简易列表失败",
        ) from exc


@router.post(
    "/",
    response_model=BaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建用户",
    tags=["用户管理"],
)
async def create_user(
    request: UserCreateRequest,
    http_request: Request,
    current_admin=Depends(get_current_admin_user),
):
    """创建用户（仅管理员/超级管理员）"""
    if request.is_admin and not await verify_super_admin(current_admin):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有超级管理员可以创建管理员")
    admin_user = await user_service.get_user_by_id(current_admin.user_id)
    try:
        user = await user_service.create_user(request)
        await audit_user_created(http_request, admin_user, user)
        return success(_build_user_response(user), message=Messages.CREATED_SUCCESS, code=201)
    except IntegrityError as e:
        err_msg = str(e)
        if "用户名" in err_msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
        if "邮箱" in err_msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已存在")
        logger.exception("创建用户发生未识别的数据完整性冲突")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户数据冲突") from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get("/{user_id}", response_model=BaseResponse, summary="获取用户详情", tags=["用户管理"])
async def get_user_detail(user_id: str, current_user: TokenData = Depends(get_current_user)):
    """获取用户详情"""
    user = await user_service.get_user_by_public_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    # 权限检查
    current_user_obj = await user_service.get_user_by_id(current_user.user_id)
    if not current_user_obj.is_admin and current_user_obj.id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")

    return success(_build_user_response(user), message=Messages.QUERY_SUCCESS)


@router.post(
    "/{user_id}/role",
    response_model=BaseResponse,
    summary="修改用户角色（仅超级管理员）",
    tags=["用户管理"],
)
async def set_user_role(
    user_id: str,
    body: AdminUserRoleUpdateRequest,
    http_request: Request,
    _admin: User = Depends(require_role(UserRole.SUPER_ADMIN)),
):
    """仅 SUPER_ADMIN 调用；专用于角色变更，自动同步 is_admin。

    强制要求 ``old_role`` + ``new_role`` 显式传参，路由层校验 ``old_role`` 与
    DB 当前 role 一致（防 stale UI），并在审计日志里同时留下 old/new 快照，
    使任何提权/降权动作都留痕。
    """
    target = await user_service.get_user_by_public_id(user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    try:
        new_role_enum = UserRole(body.new_role)
        old_role_enum = UserRole(body.old_role)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="role 取值非法")

    current_role_value = target.role.value if hasattr(target.role, "value") else str(target.role)
    if old_role_enum.value != current_role_value:
        # 与 DB 现值不一致 → 说明客户端拿到的是过期快照，拒绝以避免误改
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"old_role 与当前 role 不一致：期望 {current_role_value}，收到 {old_role_enum.value}",
        )

    # 防止 SUPER_ADMIN 把自己降级导致失去唯一超管
    if target.id == _admin.id and new_role_enum != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="超级管理员不能通过该接口降级自己",
        )

    old_is_admin = target.is_admin
    target.role = new_role_enum
    # User.save() 会按新 role 自动同步 is_admin
    await target.save(update_fields=["role", "is_admin"])
    await user_service._invalidate_user_cache(target.id)

    await audit_user_role_changed(
        http_request,
        _admin,
        target,
        old_role=old_role_enum.value,
        old_is_admin=old_is_admin,
    )
    return success(_build_user_response(target), message="角色已更新")


@router.put("/{user_id}", response_model=BaseResponse, summary="更新用户信息", tags=["用户管理"])
async def update_user(
    user_id: str,
    request: UserUpdateRequest,
    http_request: Request,
    current_user: TokenData = Depends(get_current_user),
):
    """更新用户资料（用户名 / 邮箱 / 启用状态）。

    安全约束：
    - Schema 已经通过 ``extra="forbid"`` 拒绝 ``role``/``is_admin``/``password``
      等权限或凭证字段——请求里带这些字段会直接被 Pydantic 以 422 拦截。
    - 普通 ``admin`` **不能**修改其他 admin/super_admin 的资料，只有 super_admin
      才能修改管理员账户（普通 admin 仅能改自己 + 改普通用户）。
    - 角色变更请走 ``POST /users/{id}/role``，密码变更请走
      ``POST /users/change-password`` 或 ``PUT /users/{id}/reset-password``。
    """
    current_user_obj = await user_service.get_user_by_id(current_user.user_id)
    if not current_user_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="当前用户不存在")

    target_user = await user_service.get_user_by_public_id(user_id)
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    is_self = current_user_obj.id == target_user.id
    if not current_user_obj.is_admin and not is_self:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")

    # 关键防线：admin 不能横向修改其他 admin/super_admin 的资料，只有 super_admin 才行。
    # 这堵住了「admin 通过通用更新接口踩点管理员账号」的场景。
    if not is_self and target_user.is_admin and not await verify_super_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有超级管理员可以修改管理员账户",
        )

    old_snapshot = {
        "username": target_user.username,
        "email": target_user.email,
        "is_active": target_user.is_active,
        "is_admin": target_user.is_admin,
        "role": target_user.role.value if hasattr(target_user.role, "value") else str(target_user.role),
    }

    try:
        user = await user_service.update_user(user_id, request)

        new_snapshot = {
            "username": user.username,
            "email": user.email,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        }

        username_changed = old_snapshot["username"] != new_snapshot["username"]
        operator_username = user.username if is_self else current_user_obj.username

        description = f"更新用户信息: {old_snapshot['username']}"
        if username_changed:
            description = f"更新用户信息: {old_snapshot['username']} -> {new_snapshot['username']}"

        await audit_user_updated(
            http_request,
            current_user_obj,
            user,
            operator_username=operator_username,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
            description=description,
        )

        return success(_build_user_response(user), message=Messages.UPDATED_SUCCESS)
    except IntegrityError as e:
        detail = str(e)
        if "用户名" in detail:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在") from e
        if "邮箱" in detail:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已存在") from e
        logger.exception("更新用户发生未识别的数据完整性冲突")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户数据冲突") from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


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
):
    """修改用户密码。

    该端点**仅允许用户改自己的密码**，并强制校验当前密码。管理员/超管
    重置他人密码请走 ``PUT /users/{id}/reset-password``（需 super_admin），
    以此避免「普通 admin 拿着任意 old_password 就能改超管密码」的提权路径。
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
        await user_service.update_user_password(user_id, request, current_user.user_id)
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
):
    """修改当前用户密码"""
    try:
        await user_service.update_user_password(current_user.user_id, request, current_user.user_id)
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
):
    """重置用户密码（仅超级管理员）"""
    try:
        await user_service.reset_user_password(user_id, request.new_password)
    except ValueError as exc:
        status_code = status.HTTP_404_NOT_FOUND if str(exc) == "用户不存在" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return success(None, message="密码重置成功")


@router.post(
    "/batch/status",
    response_model=BaseResponse,
    summary="批量更新用户状态",
    tags=["用户管理"],
)
async def batch_update_status(
    request: UserBatchStatusRequest,
    current_admin: TokenData = Depends(get_current_admin_user),
):
    user_ids = bounded_distinct_ids(request.user_ids, "user_ids")

    targets = await User.filter(public_id__in=user_ids).only("id", "public_id", "is_admin").all()
    _ensure_batch_status_permission(targets, current_admin)
    target_user_ids = [target.id for target in targets]
    updated = await User.filter(id__in=target_user_ids).update(is_active=request.is_active)
    if not request.is_active and target_user_ids:
        await UserSession.filter(
            user_id__in=target_user_ids,
            revoked_at__isnull=True,
        ).update(revoked_at=datetime.now())
    return success({"updated": updated}, message=Messages.UPDATED_SUCCESS)


def _ensure_batch_status_permission(targets: list[User], current_admin: TokenData) -> None:
    """普通管理员不得批量修改其他管理员账户状态。"""
    if current_admin.is_super_admin:
        return
    protected = [target for target in targets if target.is_admin and target.id != current_admin.user_id]
    if protected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有超级管理员可以修改管理员账户状态",
        )


@router.post(
    "/batch/delete",
    response_model=BaseResponse,
    summary="批量删除用户",
    tags=["用户管理"],
)
async def batch_delete_users(
    request: dict,
    current_admin: TokenData = Depends(get_current_super_admin),
):
    user_ids = bounded_distinct_ids(request.get("user_ids"), "user_ids")

    success_count = 0
    failed_ids = []
    for user_id in user_ids:
        try:
            await user_service.delete_user(user_id, current_admin.user_id)
            success_count += 1
        except Exception:
            failed_ids.append(user_id)

    return success(
        {"success_count": success_count, "failed_ids": failed_ids},
        message=Messages.DELETED_SUCCESS,
    )


@router.delete("/{user_id}", response_model=BaseResponse, summary="删除用户", tags=["用户管理"])
async def delete_user(
    user_id: str,
    http_request: Request,
    current_admin: TokenData = Depends(get_current_super_admin),
):
    """删除用户（仅超级管理员）"""
    admin_user = await user_service.get_user_by_id(current_admin.user_id)
    target_user = await user_service.get_user_by_public_id(user_id)
    try:
        await user_service.delete_user(user_id, current_admin.user_id)
        if target_user:
            await audit_user_deleted(http_request, admin_user, target_user)
        return success(None, message=Messages.DELETED_SUCCESS)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get(
    "/cache/info",
    response_model=BaseResponse,
    summary="获取缓存信息",
    tags=["用户管理"],
)
async def get_user_list_cache_info(current_admin=Depends(get_current_admin_user)):
    """获取用户列表缓存信息（仅管理员）"""
    cache_info = await user_service.get_cache_info()
    return success(cache_info, message=Messages.QUERY_SUCCESS)


@router.delete("/cache", response_model=BaseResponse, summary="清除缓存", tags=["用户管理"])
async def clear_user_list_cache(current_admin=Depends(get_current_admin_user)):
    """清除用户列表缓存（仅管理员）"""
    await user_service.clear_cache()
    return success(None, message="缓存已清除")


promote_static_routes(router, {"/cache", "/cache/info"})
