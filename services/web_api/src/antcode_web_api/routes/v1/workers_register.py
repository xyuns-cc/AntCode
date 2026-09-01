"""Worker Direct 注册接口 (POST /workers/register-direct)。

install_key 管理和 V2 注册分别由独立模块负责。
"""

from __future__ import annotations

from antcode_core.application.services.workers import worker_service
from antcode_core.common.config import settings
from antcode_core.common.exceptions import RedisConnectionError
from antcode_core.domain.schemas.worker import (
    WorkerRegisterDirectRequest,
    WorkerRegisterDirectResponse,
)
from antcode_core.infrastructure.redis import direct_register_proof_key
from fastapi import HTTPException, status
from loguru import logger

from antcode_web_api.response import BaseResponse, success


async def register_direct_worker(request: WorkerRegisterDirectRequest, *, mask_redis_url):
    """Direct Worker 注册(worker_id 作为 public_id)。"""
    if settings.REDIS_ACL_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Redis ACL 已启用，请使用安装 Key 注册并通过签名接口签发 Redis 凭据",
        )
    if not settings.REDIS_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Direct 注册需要 Redis 支持",
        )
    # delay import 让测试 monkeypatch antcode_core.infrastructure.redis.get_redis_client 生效
    from antcode_core.infrastructure.redis import get_redis_client

    try:
        redis = await get_redis_client()
    except RedisConnectionError as exc:
        logger.exception("Direct 注册 Redis 连接失败")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Direct 注册依赖服务暂不可用",
        ) from exc

    proof_key = direct_register_proof_key(request.worker_id)
    # P1-08: 原子消费 Direct 注册证明, 避免并发注册双成功
    try:
        stored_proof = await redis.getdel(proof_key)
    except Exception as exc:
        logger.exception("Direct 注册读取 Redis 证明失败: worker_id={}", request.worker_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Direct 注册依赖服务暂不可用",
        ) from exc
    if isinstance(stored_proof, (bytes, bytearray)):
        stored_proof = stored_proof.decode("utf-8")
    if not stored_proof or stored_proof != request.proof:
        logger.warning(
            "Direct 注册证明无效: worker_id={}, redis={}, exists={}",
            request.worker_id,
            mask_redis_url(settings.REDIS_URL),
            bool(stored_proof),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无效的 Direct 注册证明",
        )
    try:
        worker, created = await worker_service.register_direct_worker(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return success(
        WorkerRegisterDirectResponse(
            worker_id=worker.public_id,
            created=created,
            redis_username=None,
            redis_password=None,
        ),
        message="Direct Worker 注册成功",
    )


def register_register_routes(router, mask_redis_url) -> None:
    """Direct 注册路由挂载, install_key 相关仍在主 workers.py。"""

    @router.post(
        "/register-direct",
        response_model=BaseResponse[WorkerRegisterDirectResponse],
        summary="Direct Worker 注册",
        description="仅供未启用 Redis ACL 的可信内网 Direct Worker 使用 Redis 证明注册",
    )
    async def _register_direct_worker(request: WorkerRegisterDirectRequest):
        return await register_direct_worker(request, mask_redis_url=mask_redis_url)


__all__ = [
    "register_direct_worker",
    "register_register_routes",
]
