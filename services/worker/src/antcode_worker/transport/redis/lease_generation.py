"""Direct 传输层的 Lease 代际管理（P1-GW-03 / P1-DR-04）。

从 ``transport.py`` 拆出的 ``RedisTransport`` 代际面：续租、代际丢失
fail-closed、权威时钟与代际 guard。依赖宿主提供 ``_lease_store`` /
``_worker_id`` / ``_lease_id`` / ``_generation_lost`` / ``_keys`` /
``_task_recovery`` / ``_control_recovery`` / ``_redis`` /
``_lease_fencing_enabled`` / consumer 名字段。
"""

from __future__ import annotations

from typing import Any, NoReturn

from antcode_contracts.capabilities import encode_capabilities
from antcode_core.application.services.runtime.runtime_control_service import redis_server_now_ms
from loguru import logger

from antcode_worker.transport.generation import GenerationLostError
from antcode_worker.transport.lease_cadence import ServerLeaseCadence


class LeaseGenerationMixin(ServerLeaseCadence):
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
    _lease_revoked_callback: Any | None
    _lease_capabilities: dict[str, str]

    def set_lease_revoked_callback(self, callback: Any) -> None:
        """Register the Engine callback used for immediate Direct self-fencing."""
        self._lease_revoked_callback = callback

    def set_capabilities(self, capabilities: object) -> None:
        """Adopt the startup capability snapshot carried by every Lease call.

        与 ``GatewayTransport.set_capabilities`` 同义：Worker 自己的插件快照是
        能力的唯一权威来源，控制面只做校验与持久化。
        """
        self._lease_capabilities = encode_capabilities(capabilities)

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
            raise GenerationLostError("Direct Worker lease generation 已失效")

        try:
            grant = await direct_control.lease_renew(current_lease_id or "", metrics, self._lease_capabilities)
        except GenerationLostError as exc:
            await self._abort_generation(str(exc))
        lease_id = grant.lease_id
        expires_at_ms = grant.expires_at_ms
        renew_after_ms = grant.renew_after_ms
        # 与 Gateway 实现同一套语义：服务端权威 TTL + 节拍回传给 HeartbeatReporter。
        self.adopt_server_lease_window(grant.ttl_ms, renew_after_ms)
        if grant.revoked:
            await self._abort_generation("Direct control revoked the current Worker lease")
        if self._lease_id and lease_id != self._lease_id:
            logger.error(f"Direct Lease 代际切换被拒绝: old={self._lease_id} new={lease_id}，本进程按撤销处理")
            await self._abort_generation("Direct control returned a different Worker lease generation")
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
        if self._generation_lost:
            return False
        if not self._lease_fencing_enabled:
            return True
        if not self._lease_store or not self._worker_id or not self._lease_id:
            return False
        current = bool(await self._lease_store.is_current(self._worker_id, self._lease_id))
        if not current:
            # 这条判定会让 Worker 自杀停机，必须能事后归因到具体判据，
            # 否则只能看到 "generation 已失效" 而无从区分换代/撤销/围栏/TTL。
            logger.error(
                "代际自检失败: worker_id={} local_lease={} fencing={}",
                self._worker_id,
                self._lease_id,
                self._lease_fencing_enabled,
            )
        return current

    async def _require_current_generation(self) -> None:
        if not await self._is_current_generation():
            raise GenerationLostError("Direct Worker lease generation 已失效")

    async def _abort_generation(self, reason: str) -> NoReturn:
        # 置位后所有 lease_renew 都会在入口直接抛错、续期循环终止、Worker 停机。
        # 记录触发原因，才能区分是外部换代/撤销，还是自检误报。
        logger.error("代际作废: worker_id={} lease={} reason={}", self._worker_id, self._lease_id, reason)
        self._generation_lost = True
        callback = self._lease_revoked_callback
        if callback is not None:
            try:
                await callback(reason)
            except Exception as exc:
                raise GenerationLostError(reason) from exc
        raise GenerationLostError(reason)
