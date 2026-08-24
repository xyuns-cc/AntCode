"""Deregister 的代际守卫与心跳清理（从 control_service.Deregister 拆出）。

空 ``lease_id`` 会让 ``LeaseStore.revoke`` 走 ``REVOKE_LUA`` 的 ``expected_id==''``
分支：跳过代际匹配、无条件 ``DEL`` 任意 worker 的 lease（``lease_scripts.py``）。
受控 Redis 实测证明这是持伪造证书者接管**活跃** worker 的前置步骤——先撤掉受害者
当前 lease、绕过 ``grant`` 的 conflict 检查，再自签新 lease 完成接管。因此这条
worker-facing 的 Deregister RPC 必须携带当前代际；空值的强制撤销只保留给 Master
进程内直调 ``LeaseStore.revoke``，不经不可信的 RPC 边界。
"""

from __future__ import annotations

from typing import Any

import grpc
from loguru import logger

from antcode_gateway.security_audit import (
    EVENT_DEREGISTER_MISSING_GENERATION,
    SecurityAuditEvent,
    SecurityAuditor,
)

# 结构化拒绝码。日志/审计/测试一律匹配这个常量，不匹配中文描述（描述会漂移）。
DEREGISTER_MISSING_GENERATION = "DEREGISTER_MISSING_GENERATION"


async def reject_deregister_without_generation(
    context: grpc.aio.ServicerContext,
    auditor: SecurityAuditor,
    worker_id: str,
) -> None:
    """留证并拒绝：结构化码 + 审计事件（worker_id/peer 可追溯），再 abort。"""
    peer = str(context.peer() or "")
    logger.warning(
        "Deregister 拒绝(缺当前代际 lease_id): code={} worker_id={} peer={}",
        DEREGISTER_MISSING_GENERATION,
        worker_id,
        peer,
    )
    await auditor.emit(
        SecurityAuditEvent(
            event_type=EVENT_DEREGISTER_MISSING_GENERATION,
            worker_id=worker_id,
            peer=peer,
            reason=DEREGISTER_MISSING_GENERATION,
        )
    )
    await context.abort(grpc.StatusCode.INVALID_ARGUMENT, DEREGISTER_MISSING_GENERATION)


async def delete_deregister_heartbeat(redis: Any, worker_id: str) -> None:
    """撤租后清理过渡期心跳 Hash（运维 dashboard 兼容）；失败只告警不阻塞。"""
    from antcode_core.infrastructure.redis import worker_heartbeat_key

    if redis is None:
        return
    try:
        await redis.delete(worker_heartbeat_key(worker_id))
    except Exception as exc:  # noqa: BLE001 - 心跳清理是尽力而为，失败不影响撤租结果
        logger.warning(f"Deregister 清理 heartbeat 失败: {exc}")


__all__ = [
    "DEREGISTER_MISSING_GENERATION",
    "delete_deregister_heartbeat",
    "reject_deregister_without_generation",
]
