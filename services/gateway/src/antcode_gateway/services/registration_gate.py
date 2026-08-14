"""Gateway registration acknowledgement policy.

``/antcode.v1.ControlService/Register`` 在 ``AuthInterceptor.AUTH_EXEMPT_METHODS``
里，拦截器完全不介入：worker 首次注册时还没有可放进 metadata 的 api_key。
因此**唯一**的凭据防线就是本模块 —— 用请求体里的 ``api_key`` 走 verify_api_key，
再叠加 Worker 状态与 V2 注册 ACK 校验。相应地，Register 的安全审计也只能由
调用方（``GatewayControlService.Register``）自己发出。
"""

from antcode_core.application.services.workers.worker_registration_gate import (
    has_unacknowledged_v2_registration,
)
from antcode_core.common.security.api_key import verify_api_key
from antcode_core.domain.models import Worker, WorkerStatus
from loguru import logger

from antcode_gateway.auth_credentials import INVALID_API_KEY_ERROR
from antcode_gateway.security_audit import (
    EVENT_API_KEY_INVALID,
    EVENT_REGISTER_REJECTED,
    SecurityAuditEvent,
    SecurityAuditor,
)

UNACKNOWLEDGED_REGISTRATION_ERROR = "Worker V2 registration is not acknowledged"


async def registration_acknowledged(worker_id: str) -> bool:
    return not await has_unacknowledged_v2_registration(worker_id)


async def registration_rejection(worker_id: str) -> str | None:
    if await registration_acknowledged(worker_id):
        return None
    logger.warning("Register 拒绝未 ACK 的 V2 Worker: worker_id={}", worker_id)
    return UNACKNOWLEDGED_REGISTRATION_ERROR


async def registration_validation_error(api_key: str, worker_id: str) -> str | None:
    """Validate the API key binding and durable V2 registration state."""
    try:
        if not await verify_api_key(api_key, worker_id or None):
            return INVALID_API_KEY_ERROR
        worker = await Worker.filter(public_id=worker_id).only("public_id", "status").first()
        if worker is None:
            return "Worker 不存在"
        if worker_id and worker.public_id != worker_id:
            return "Worker ID 不匹配"
        if worker.status not in {WorkerStatus.CONNECTING.value, WorkerStatus.ONLINE.value}:
            return f"Worker 状态不允许注册: {worker.status}"
        return await registration_rejection(worker_id)
    except Exception:
        logger.exception("Register Worker/API Key 验证服务异常: worker_id={}", worker_id)
        return "Worker/API Key 验证服务不可用"


def registration_audit_event(error: str) -> str:
    """把 Register 拒绝原因映射到安全审计事件类型。

    API Key 不匹配单独归到 ``api_key_invalid``，与拦截器侧的凭据失败共用同一
    事件类型，凭据爆破检测规则不必区分入口。其余（Worker 不存在 / 状态不允许 /
    未 ACK / 依赖不可用）归到 ``register_rejected``。
    """
    if error == INVALID_API_KEY_ERROR:
        return EVENT_API_KEY_INVALID
    return EVENT_REGISTER_REJECTED


async def audit_registration_rejection(
    auditor: SecurityAuditor,
    worker_id: str,
    *,
    peer: str,
    error: str,
) -> None:
    """记录一次 Register 拒绝。

    Register 免认证，AuthInterceptor 不会为它写 ``audit:security``；针对注册
    接口的 API Key 爆破只有这里能观测到，缺了它就完全没有告警数据源。
    ``peer`` 取真实传输对端，客户端伪造不了。
    """
    logger.warning("Register 验证失败: worker_id={}, error={}", worker_id, error)
    await auditor.emit(
        SecurityAuditEvent(
            event_type=registration_audit_event(error),
            worker_id=worker_id,
            peer=peer,
            reason=error,
        )
    )


__all__ = [
    "UNACKNOWLEDGED_REGISTRATION_ERROR",
    "audit_registration_rejection",
    "registration_acknowledged",
    "registration_audit_event",
    "registration_rejection",
    "registration_validation_error",
]
