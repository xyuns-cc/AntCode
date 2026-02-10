"""运行时管理接口"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from antcode_web_api.response import success as success_response
from antcode_core.common.security.auth import get_current_user, get_current_user_id
from antcode_core.domain.models.enums import RuntimeScope
from antcode_core.domain.models.worker import Worker
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.application.services.workers.worker_service import worker_service
from antcode_core.application.services.runtime import runtime_control_service
from antcode_core.application.services.users.user_service import user_service

runtime_router = APIRouter()


class CreateEnvRequest(BaseModel):
    scope: RuntimeScope = Field(..., description="运行时作用域")
    python_version: str = Field(..., description="Python 版本")
    env_name: str | None = Field(None, description="环境名称")
    packages: list[str] = Field(default_factory=list, description="初始依赖")


class PackageRequest(BaseModel):
    packages: list[str] = Field(default_factory=list, description="包列表")
    upgrade: bool = Field(False, description="是否升级安装")


class EnvUpdateRequest(BaseModel):
    key: str | None = None
    description: str | None = None


class RegisterInterpreterRequest(BaseModel):
    python_bin: str = Field(..., description="Python 可执行路径")
    version: str | None = Field(None, description="Python 版本（可选）")


class InstallInterpreterRequest(BaseModel):
    version: str = Field(..., description="Python 版本")


async def _ensure_worker_access(worker_id: str, user_id: int) -> Worker:
    worker = await Worker.get_or_none(public_id=worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Worker 不存在")
    if worker.status != "online":
        raise HTTPException(status_code=400, detail=f"Worker {worker.name} 当前不在线")

    is_admin = await user_service.is_admin(user_id)
    if not is_admin:
        allowed = await worker_service.check_user_worker_permission(
            user_id=user_id, worker_id=worker.id, is_admin=False, required_permission="use"
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="无 Worker 访问权限")

    return worker


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
    _ = current_user
    await _ensure_worker_access(worker_id, current_user_id)

    result = await runtime_control_service.list_envs(worker_id, scope=scope)
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"获取环境失败: {result.get('error') or '未知错误'}",
        )

    envs = result.get("data") or []
    if scope:
        envs = [env for env in envs if env.get("scope") == scope]
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
    """创建运行时环境"""
    worker = await _ensure_worker_access(worker_id, current_user_id)

    env_name = payload.env_name
    scope = payload.scope.value
    python_version = payload.python_version

    if scope == RuntimeScope.SHARED.value:
        if env_name and not env_name.startswith("shared-"):
            raise HTTPException(status_code=400, detail="共享环境名称必须以 shared- 开头")
        env_name = env_name or f"shared-py{python_version.replace('.', '')}"
    else:
        if env_name and env_name.startswith("shared-"):
            raise HTTPException(status_code=400, detail="私有环境名称不允许以 shared- 开头")
        env_name = env_name or f"private-{uuid.uuid4().hex[:8]}-py{python_version.replace('.', '')}"

    created_by = current_user.username if current_user else str(current_user_id)

    result = await runtime_control_service.create_env(
        worker_id=worker_id,
        env_name=env_name,
        python_version=python_version,
        packages=payload.packages,
        created_by=created_by,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"创建环境失败: {result.get('error') or '未知错误'}",
        )

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
    _ = current_user
    await _ensure_worker_access(worker_id, current_user_id)

    result = await runtime_control_service.get_env(worker_id, env_name)
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"获取环境失败: {result.get('error') or '未知错误'}",
        )

    return success_response(result.get("data") or {})


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
    _ = current_user
    await _ensure_worker_access(worker_id, current_user_id)

    result = await runtime_control_service.update_env(
        worker_id,
        env_name,
        key=payload.key,
        description=payload.description,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"更新环境失败: {result.get('error') or '未知错误'}",
        )
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
    """删除运行时环境"""
    _ = current_user
    await _ensure_worker_access(worker_id, current_user_id)

    result = await runtime_control_service.delete_env(worker_id, env_name)
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"删除环境失败: {result.get('error') or '未知错误'}",
        )

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
    _ = current_user
    await _ensure_worker_access(worker_id, current_user_id)

    result = await runtime_control_service.list_packages(worker_id, env_name)
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"获取依赖失败: {result.get('error') or '未知错误'}",
        )

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
    """安装依赖"""
    _ = current_user
    await _ensure_worker_access(worker_id, current_user_id)
    if not payload.packages:
        raise HTTPException(status_code=400, detail="必须提供依赖列表")

    result = await runtime_control_service.install_packages(
        worker_id, env_name, payload.packages, upgrade=payload.upgrade
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"安装依赖失败: {result.get('error') or '未知错误'}",
        )

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
    """卸载依赖"""
    _ = current_user
    await _ensure_worker_access(worker_id, current_user_id)
    if not payload.packages:
        raise HTTPException(status_code=400, detail="必须提供依赖列表")

    result = await runtime_control_service.uninstall_packages(
        worker_id, env_name, payload.packages
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"卸载依赖失败: {result.get('error') or '未知错误'}",
        )

    return success_response(result.get("data") or {})


@runtime_router.get(
    "/workers/{worker_id}/interpreters",
    response_model=BaseResponse[list[dict[str, Any]]],
)
async def list_interpreters(
    worker_id: str,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """列出可用解释器"""
    _ = current_user
    await _ensure_worker_access(worker_id, current_user_id)

    result = await runtime_control_service.list_interpreters(worker_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"获取解释器失败: {result.get('error') or '未知错误'}",
        )

    return success_response(result.get("data") or [])


@runtime_router.post(
    "/workers/{worker_id}/interpreters",
    response_model=BaseResponse[dict[str, Any]],
)
async def install_interpreter(
    worker_id: str,
    payload: InstallInterpreterRequest,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """安装解释器"""
    _ = current_user
    await _ensure_worker_access(worker_id, current_user_id)

    result = await runtime_control_service.install_interpreter(worker_id, payload.version)
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"安装解释器失败: {result.get('error') or '未知错误'}",
        )

    return success_response(result.get("data") or {})


@runtime_router.post(
    "/workers/{worker_id}/interpreters/register",
    response_model=BaseResponse[dict[str, Any]],
)
async def register_interpreter(
    worker_id: str,
    payload: RegisterInterpreterRequest,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """注册本地解释器"""
    _ = current_user
    await _ensure_worker_access(worker_id, current_user_id)

    result = await runtime_control_service.register_interpreter(
        worker_id, python_bin=payload.python_bin, version=payload.version
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"注册解释器失败: {result.get('error') or '未知错误'}",
        )

    return success_response(result.get("data") or {})


@runtime_router.get(
    "/workers/{worker_id}/python-versions",
    response_model=BaseResponse[dict[str, Any]],
)
async def get_worker_python_versions(
    worker_id: str,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """获取 Worker Python 版本信息"""
    _ = current_user
    await _ensure_worker_access(worker_id, current_user_id)

    result = await runtime_control_service.get_python_versions(worker_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"获取版本失败: {result.get('error') or '未知错误'}",
        )

    data = result.get("data") or {}
    platform_raw = data.get("platform") or {}
    data["platform"] = {
        "os_type": platform_raw.get("system") or "",
        "os_version": platform_raw.get("release") or "",
        "python_version": platform_raw.get("python_version") or "",
        "machine": platform_raw.get("machine") or "",
        "mise_available": bool(platform_raw.get("mise_available", False)),
    }
    return success_response(data)


@runtime_router.post(
    "/workers/{worker_id}/python-versions/{version}/install",
    response_model=BaseResponse[dict[str, Any]],
)
async def install_worker_python_version(
    worker_id: str,
    version: str,
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """安装 Worker Python 版本"""
    _ = current_user
    await _ensure_worker_access(worker_id, current_user_id)

    result = await runtime_control_service.install_interpreter(worker_id, version)
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"安装解释器失败: {result.get('error') or '未知错误'}",
        )

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
    await _ensure_worker_access(worker_id, current_user_id)

    result = await runtime_control_service.get_platform_info(worker_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"获取平台信息失败: {result.get('error') or '未知错误'}",
        )

    data = result.get("data") or {}
    platform_info = {
        "os_type": data.get("system") or "",
        "os_version": data.get("release") or "",
        "python_version": data.get("python_version") or "",
        "machine": data.get("machine") or "",
        "mise_available": bool(data.get("mise_available", False)),
    }
    return success_response(platform_info)


@runtime_router.delete(
    "/workers/{worker_id}/interpreters",
    response_model=BaseResponse[dict[str, Any]],
)
async def uninstall_interpreter(
    worker_id: str,
    version: str | None = Query(None, description="解释器版本"),
    python_bin: str | None = Query(None, description="解释器路径"),
    mode: str = Query("uninstall", description="uninstall/unregister"),
    current_user_id=Depends(get_current_user_id),
    current_user=Depends(get_current_user),
):
    """卸载/移除解释器"""
    _ = current_user
    await _ensure_worker_access(worker_id, current_user_id)

    if mode == "unregister":
        if not python_bin and not version:
            raise HTTPException(status_code=400, detail="移除注册必须指定 python_bin 或 version")
        result = await runtime_control_service.unregister_interpreter(
            worker_id, version=version, python_bin=python_bin
        )
    else:
        if not version:
            raise HTTPException(status_code=400, detail="卸载解释器必须指定 version")
        result = await runtime_control_service.uninstall_interpreter(worker_id, version)

    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"处理解释器失败: {result.get('error') or '未知错误'}",
        )

    return success_response(result.get("data") or {})


router = runtime_router

__all__ = ["runtime_router", "router"]
