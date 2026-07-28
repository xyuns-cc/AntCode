"""Direct 传输层的 Lease 代际管理（P1-GW-03 / P1-DR-04）。

从 ``transport.py`` 拆出的 ``RedisTransport`` 代际面：续租、代际丢失
fail-closed、权威时钟与代际 guard。依赖宿主提供 ``_lease_store`` /
``_worker_id`` / ``_lease_id`` / ``_generation_lost`` / ``_keys`` /
``_task_recovery`` / ``_control_recovery`` / ``_redis`` /
``_lease_fencing_enabled`` / consumer 名字段。
"""

from __future__ import annotations

from typing import Any

from antcode_core.application.services.runtime.runtime_control_service import redis_server_now_ms
from loguru import logger


class LeaseGenerationMixin:
    """Direct Lease 代际管理混入。"""

    # 宿主（RedisTransport）提供的契约面。
    _lease_store: Any
    _worker_id: str | None
    _lease_id: str
    _generation_lost: bool
    _lease_fencing_enabled: bool
    _redis: Any
    _keys: Any
    _task_recovery: Any
    _control_recovery: Any
    _task_consumer_name: str
    _control_consumer_name: str

    async def lease_renew(
        self,
        current_lease_id: str,
        metrics: dict | None = None,
    ) -> tuple[str, int, int, bool]:
        """Direct 模式通过可信 HTTP 控制面签发或续租 Lease。

        Returns ``(new_lease_id, expires_at_ms, renew_after_ms, revoked)``；
        ``revoked=True`` 仅在代际切换被拒绝（P1-GW-03）时出现。
        """
        if not self._lease_store or not self._worker_id:
            raise RuntimeError("Direct Lease 只读状态未初始化")
        direct_control = getattr(self, "_direct_control", None)
        if direct_control is None:
            raise RuntimeError("Direct control client 未配置")
        if self._generation_lost:
            # P1-GW-03: 代际已丢失，不再 grant（避免反复签发新代际）。
            return ("", 0, 0, True)

        lease_id, expires_at_ms, renew_after_ms, revoked = await direct_control.lease_renew(
            current_lease_id or "",
            metrics,
        )
        if revoked:
            self._generation_lost = True
            return ("", 0, 0, True)
        if self._lease_id and lease_id != self._lease_id:
            # P1-GW-03: 换代拒绝采用；在途 run 经 GenerationLostError fail-closed 中止。
            self._generation_lost = True
            logger.error(f"Direct Lease 代际切换被拒绝: old={self._lease_id} new={lease_id}，本进程按撤销处理")
            return ("", 0, 0, True)
        self._lease_id = lease_id
        task_consumer_name = self._keys.consumer_name(self._worker_id, lease_id)
        if task_consumer_name != self._task_consumer_name:
            self._task_consumer_name = task_consumer_name
            self._task_recovery.reset()
        if task_consumer_name != self._control_consumer_name:
            self._control_consumer_name = task_consumer_name
            self._control_recovery.reset()
        return (
            lease_id,
            expires_at_ms,
            renew_after_ms,
            False,
        )

    async def authoritative_now_ms(self) -> int:
        """P1-DR-04: Direct 模式以 Redis TIME 为 deadline 判定时钟。"""
        if not self._redis:
            raise RuntimeError("Redis 未连接，无法读取权威时钟")
        return await redis_server_now_ms(self._redis)

    async def _is_current_generation(self) -> bool:
        if not self._lease_fencing_enabled:
            return True
        if not self._lease_store or not self._worker_id or not self._lease_id:
            return False
        return bool(await self._lease_store.is_current(self._worker_id, self._lease_id))

    async def _require_current_generation(self) -> None:
        if not await self._is_current_generation():
            from antcode_worker.transport.base import GenerationLostError

            raise GenerationLostError("Direct Worker lease generation 已失效")
