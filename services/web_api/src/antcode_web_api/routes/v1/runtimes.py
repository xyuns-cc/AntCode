"""运行时管理接口"""

from __future__ import annotations

import re
from typing import Any, Never

from antcode_core.application.services.runtime import runtime_control_service
from antcode_core.common.security.auth import get_current_user, get_current_user_id
from antcode_core.domain.schemas.common import BaseResponse
from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger

from antcode_web_api.response import success as success_response
from antcode_web_api.routes.v1.runtime_access import (
    can_access_runtime,
    ensure_runtime_mutation_access,
    ensure_worker_access,
    ensure_worker_admin_access,
    fetch_accessible_runtime,
)
from antcode_web_api.routes.v1.runtime_audit import (
    audit_package_install,
    audit_package_uninstall,
    audit_runtime_create,
    audit_runtime_delete,
    audit_runtime_update,
)
from antcode_web_api.routes.v1.runtime_models import (
    CreateEnvRequest,
    EnvUpdateRequest,
    PackageRequest,
    build_platform_info,
    resolve_env_name,
)

runtime_router = APIRouter()

# 环境名白名单：字母/数字/点/下划线/连字符，长度 1-64
# 与 worker 侧 uv_manager._ENV_NAME_PATTERN 保持一致，防止路径遍历攻击
_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _raise_runtime_failure(operation: str, result: dict[str, Any]) -> Never:
    logger.error("Worker 运行时操作失败: operation={}, error={}", operation, result.get("error"))
    raise HTTPException(status_code=500, detail=f"{operation}失败")


def _validate_env_name(env_name: str) -> str:
    """API 边界：拒绝任何可能导致路径遍历的 env_name。"""
    if not isinstance(env_name, str) or not env_name:
        raise HTTPException(status_code=400, detail=f"非法环境名: {env_name!r}")
    if env_name in {".", ".."}:
        raise HTTPException(status_code=400, detail=f"非法环境名: {env_name!r}")
    if not _ENV_NAME_PATTERN.match(env_name):
        raise HTTPException(status_code=400, detail=f"非法环境名: {env_name!r}")
    return env_name


@runtime_router.get(
    "/workers/{worker_id}/runtimes",
    response_model=BaseResponse[list[dict[str, Any]]],
)
async def list_envs(
    worker_id: str,
    scope: str | None = Query(None, description="shared/private"),
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """列出 Worker 上的运行时环境"""
    await ensure_worker_access(worker_id, current_user_id)

    result = await runtime_control_service.list_envs(worker_id, scope=scope)
    if not result.get("success"):
        _raise_runtime_failure("获取环境", result)

    envs = result.get("data") or []
    if scope:
        envs = [env for env in envs if env.get("scope") == scope]
    envs = [env for env in envs if can_access_runtime(env, current_user)]
    return success_response(envs)


@runtime_router.post(
    "/workers/{worker_id}/runtimes",
    response_model=BaseResponse[dict[str, Any]],
    status_code=status.HTTP_201_CREATED,
)
async def create_env(
    worker_id: str,
    payload: CreateEnvRequest,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """创建运行时环境（P0-01：依赖构建面 → 管理员）"""
    worker = await ensure_worker_admin_access(worker_id, current_user_id)

    scope = payload.scope.value
    python_version = payload.python_version
    env_name = resolve_env_name(scope, python_version, payload.env_name)
    _validate_env_name(env_name)
    created_by = current_user.username if current_user else str(current_user_id)

    result = await runtime_control_service.create_env(
        worker_id=worker_id,
        env_name=env_name,
        python_version=python_version,
        packages=payload.packages,
        created_by=created_by,
        owner_user_id=str(current_user_id),
    )
    if not result.get("success"):
        _raise_runtime_failure("创建环境", result)

    await audit_runtime_create(worker_id, env_name, current_user)

    return success_response(
        {
            "worker_id": worker.public_id,
            "env": result.get("data") or {},
        },
        code=201,
    )


@runtime_router.get(
    "/workers/{worker_id}/runtimes/{env_name}",
    response_model=BaseResponse[dict[str, Any]],
)
async def get_env_detail(
    worker_id: str,
    env_name: str,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """获取运行时环境详情"""
    _validate_env_name(env_name)
    await ensure_worker_access(worker_id, current_user_id)

    env = await fetch_accessible_runtime(worker_id, env_name, current_user)
    return success_response(env)


@runtime_router.patch(
    "/workers/{worker_id}/runtimes/{env_name}",
    response_model=BaseResponse[dict[str, Any]],
)
async def update_env_detail(
    worker_id: str,
    env_name: str,
    payload: EnvUpdateRequest,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """更新环境元数据"""
    _validate_env_name(env_name)
    await ensure_worker_access(worker_id, current_user_id)

    env = await fetch_accessible_runtime(worker_id, env_name, current_user)
    ensure_runtime_mutation_access(env, current_user)

    result = await runtime_control_service.update_env(
        worker_id,
        env_name,
        key=payload.key,
        description=payload.description,
    )
    if not result.get("success"):
        _raise_runtime_failure("更新环境", result)
    await audit_runtime_update(worker_id, env_name, current_user)
    return success_response(result.get("data") or {})


@runtime_router.delete(
    "/workers/{worker_id}/runtimes/{env_name}",
    response_model=BaseResponse[dict[str, Any]],
)
async def delete_env(
    worker_id: str,
    env_name: str,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """删除运行时环境（P0-01：影响所有用户任务 → 管理员）"""
    _validate_env_name(env_name)
    await ensure_worker_admin_access(worker_id, current_user_id)

    result = await runtime_control_service.delete_env(worker_id, env_name)
    if not result.get("success"):
        _raise_runtime_failure("删除环境", result)

    await audit_runtime_delete(worker_id, env_name, current_user)

    return success_response({"deleted": True})


@runtime_router.get(
    "/workers/{worker_id}/runtimes/{env_name}/packages",
    response_model=BaseResponse[list[dict[str, Any]]],
)
async def list_packages(
    worker_id: str,
    env_name: str,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """列出环境依赖"""
    _validate_env_name(env_name)
    await ensure_worker_access(worker_id, current_user_id)

    await fetch_accessible_runtime(worker_id, env_name, current_user)

    result = await runtime_control_service.list_packages(worker_id, env_name)
    if not result.get("success"):
        _raise_runtime_failure("获取依赖", result)

    return success_response(result.get("data") or [])


@runtime_router.post(
    "/workers/{worker_id}/runtimes/{env_name}/packages",
    response_model=BaseResponse[dict[str, Any]],
)
async def install_packages(
    worker_id: str,
    env_name: str,
    payload: PackageRequest,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """安装依赖（P0-01：uv pip install 可执行 sdist/PEP517 build backend/VCS hook 在 Worker 主 UID 下 → 管理员）"""
    _validate_env_name(env_name)
    await ensure_worker_admin_access(worker_id, current_user_id)
    if not payload.packages:
        raise HTTPException(status_code=400, detail="必须提供依赖列表")

    result = await runtime_control_service.install_packages(
        worker_id, env_name, payload.packages, upgrade=payload.upgrade
    )
    if not result.get("success"):
        _raise_runtime_failure("安装依赖", result)

    await audit_package_install(worker_id, env_name, current_user)

    return success_response(result.get("data") or {})


@runtime_router.delete(
    "/workers/{worker_id}/runtimes/{env_name}/packages",
    response_model=BaseResponse[dict[str, Any]],
)
async def uninstall_packages(
    worker_id: str,
    env_name: str,
    payload: PackageRequest,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """卸载依赖（P0-01：依赖管理面 → 管理员）"""
    _validate_env_name(env_name)
    await ensure_worker_admin_access(worker_id, current_user_id)
    if not payload.packages:
        raise HTTPException(status_code=400, detail="必须提供依赖列表")

    result = await runtime_control_service.uninstall_packages(worker_id, env_name, payload.packages)
    if not result.get("success"):
        _raise_runtime_failure("卸载依赖", result)

    await audit_package_uninstall(worker_id, env_name, current_user)

    return success_response(result.get("data") or {})


@runtime_router.get(
    "/workers/{worker_id}/platform",
    response_model=BaseResponse[dict[str, Any]],
)
async def get_worker_platform(
    worker_id: str,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """获取 Worker 平台信息"""
    _ = current_user
    await ensure_worker_access(worker_id, current_user_id)

    result = await runtime_control_service.get_platform_info(worker_id)
    if not result.get("success"):
        _raise_runtime_failure("获取平台信息", result)

    return success_response(build_platform_info(result.get("data") or {}))


router = runtime_router

__all__ = ["runtime_router", "router"]
