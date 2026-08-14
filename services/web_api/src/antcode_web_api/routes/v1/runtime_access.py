"""Worker access checks for runtime routes."""

from __future__ import annotations

from antcode_core.application.services.runtime import runtime_control_service
from antcode_core.application.services.users.user_service import user_service
from antcode_core.application.services.workers.worker_service import worker_service
from antcode_core.common.security.auth import TokenData
from antcode_core.domain.models.worker import Worker
from fastapi import HTTPException
from loguru import logger


async def ensure_worker_use_access(worker_id: str, user_id: int) -> Worker:
    """校验用户是否可将任务绑定到指定 Worker。"""
    worker = await Worker.get_or_none(public_id=worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Worker 不存在")

    is_admin = await user_service.is_admin(user_id)
    if is_admin:
        return worker

    allowed = await worker_service.check_user_worker_permission(
        user_id=user_id,
        worker_id=worker.id,
        is_admin=False,
        required_permission="use",
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="无 Worker 访问权限")
    return worker


async def ensure_worker_access(worker_id: str, user_id: int) -> Worker:
    worker = await ensure_worker_use_access(worker_id, user_id)
    if worker.status != "online":
        raise HTTPException(status_code=400, detail=f"Worker {worker.name} 当前不在线")
    return worker


def can_access_runtime(env: dict, current_user: TokenData) -> bool:
    """管理员可访问全部；普通用户仅可访问 shared 或本人 private 环境。"""
    if current_user.is_admin:
        return True
    scope = env.get("scope")
    if scope == "shared":
        return True
    return scope == "private" and _is_runtime_owner(env, current_user.user_id)


def _is_runtime_owner(env: dict, user_id: int) -> bool:
    """仅用不可复用用户主键授权；旧 username 清单对普通用户 fail-closed。"""
    owner_user_id = env.get("owner_user_id")
    return owner_user_id is not None and str(owner_user_id) == str(user_id)


def ensure_runtime_access(env: dict, current_user: TokenData) -> None:
    """对缺失/未知 scope 采用拒绝策略，避免 Worker 异常响应扩大权限。"""
    if not can_access_runtime(env, current_user):
        raise HTTPException(status_code=403, detail="无权访问该私有运行时环境")


def ensure_runtime_mutation_access(env: dict, current_user: TokenData) -> None:
    """Shared 仅管理员可改，private 仅创建者可改。"""
    scope = env.get("scope")
    if scope == "shared" and current_user.is_admin:
        return
    if scope == "private" and _is_runtime_owner(env, current_user.user_id):
        return
    raise HTTPException(status_code=403, detail="无权修改该运行时环境")


async def fetch_accessible_runtime(
    worker_id: str,
    env_name: str,
    current_user: TokenData,
) -> dict:
    result = await runtime_control_service.get_env(worker_id, env_name)
    if not result.get("success"):
        logger.error("获取 Worker 运行时环境失败: worker_id={}, error={}", worker_id, result.get("error"))
        raise HTTPException(status_code=500, detail="获取环境失败")
    env = result.get("data") or {}
    ensure_runtime_access(env, current_user)
    return env


async def ensure_worker_admin_access(worker_id: str, user_id: int) -> Worker:
    """
    P0-01：装包/卸包等会触发依赖构建脚本的操作必须限制为管理员。
    普通 `use` 权限只允许执行任务（进任务沙箱），不允许在 Worker 主 UID 下
    通过 uv/pip 触发 sdist / PEP 517 build backend / VCS 依赖执行任意代码。
    """
    worker = await Worker.get_or_none(public_id=worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Worker 不存在")
    if worker.status != "online":
        raise HTTPException(status_code=400, detail=f"Worker {worker.name} 当前不在线")

    is_admin = await user_service.is_admin(user_id)
    if not is_admin:
        raise HTTPException(
            status_code=403,
            detail="仅管理员可执行依赖管理操作（装包/卸包会在 Worker 主机上执行构建脚本，需受信主体）",
        )
    return worker
