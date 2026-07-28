"""T7-B3a (P1-1): 派发失败补派 loop（leader-only）。

覆盖两条链路的补派：
- `scheduler_service._execute_task_internal` 派发失败 → 入队
- `batch_dispatcher_service._dispatch_single_url_run` 派发失败 → 入队

本 loop 每 10s tick 一次,从 ``{ns}:task:redispatch`` ZSet 拿到期任务,逐个
调 ``worker_task_dispatcher.dispatch_task`` 重派：
- 成功 → ``ack`` 从 processing hash 移除
- 仍失败 → attempts++ ``enqueue`` 新 payload 再 ``ack`` 旧 raw_payload
  (原子先入后出,防止丢单;指数退避见 redispatch_service.next_delay_seconds)
- 超阈值 → 触发放弃：置 TaskRun.FAILED + 写 audit_log + 告警

**P1-18 修复**:每轮 tick 先 ``sweep_stalled()`` 恢复上轮崩溃遗留的 claim,
再 ``claim_due()`` 原子拿新任务;处理成功走 ``ack``,处理失败走
``ack + enqueue``;handler 本身抛异常走 ``requeue_raw``(不动 attempts)。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from antcode_core.application.services.scheduler.redispatch_service import (
    redispatch_service,
)
from antcode_core.common.config import settings
from antcode_core.domain.models.enums import DispatchStatus
from antcode_core.domain.models.task_run import TaskRun
from loguru import logger

from antcode_master.leader import ensure_leader


class RedispatchLoop:
    """派发失败补派 loop。"""

    def __init__(self, tick_interval_seconds: float | None = None) -> None:
        configured_interval = (
            tick_interval_seconds
            if tick_interval_seconds is not None
            else getattr(settings, "REDISPATCH_TICK_INTERVAL_SEC", 10)
        )
        if configured_interval is None:
            raise ValueError("REDISPATCH_TICK_INTERVAL_SEC 不能为空")
        self._tick_interval = float(configured_interval)
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"补派 loop 已启动: tick={self._tick_interval}s")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        logger.info("补派 loop 已停止")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                if not await ensure_leader():
                    await asyncio.sleep(self._tick_interval)
                    continue
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"补派 loop 异常: {exc}")
            await asyncio.sleep(self._tick_interval)

    async def _tick(self) -> None:
        # 崩溃恢复:上轮 leader crash / 切主 / claim 后异常都会在 processing
        # hash 留下漂浮 entry,这里把超时的捞回 ZSet 再走一遍。
        try:
            await redispatch_service.sweep_stalled()
        except Exception as exc:
            logger.warning(f"redispatch sweep_stalled 异常: {exc}")

        due = await redispatch_service.claim_due(limit=50)
        if not due:
            return
        logger.info(f"补派 tick: 到期 {len(due)} 项")
        for payload in due:
            raw_payload = payload.get("__raw_payload", "")
            try:
                handled = await self._handle_one(payload)
                if handled:
                    # 成功或已重排/放弃 -> 从 processing hash 移除
                    if raw_payload:
                        await redispatch_service.ack(raw_payload)
            except Exception as exc:
                logger.exception(f"补派单项处理异常: run_id={payload.get('run_id')} err={exc}")
                # 处理本身异常: raw_payload 放回 ZSet(不改 attempts),下轮重来
                if raw_payload:
                    await redispatch_service.requeue_raw(raw_payload, delay_seconds=int(self._tick_interval))

    async def _handle_one(self, payload: dict[str, Any]) -> bool:
        """处理单条:返回 True 表示已终结(需要 ack),False 表示尚未终结。

        终结状态包括:派发成功、已 re-enqueue 新 payload、已放弃。
        """
        from antcode_core.application.services.workers.worker_dispatcher import (
            worker_task_dispatcher,
        )

        run_id = payload.get("run_id") or ""
        project_id = payload.get("project_id") or ""
        if not run_id or not project_id:
            logger.warning(f"补派 payload 缺关键字段,丢弃: {payload}")
            return True

        result = await worker_task_dispatcher.dispatch_task(
            project_id=project_id,
            run_id=run_id,
            params=payload.get("params") or {},
            environment_vars=payload.get("environment_vars") or None,
            runtime_env_name=payload.get("runtime_env_name") or None,
            timeout=int(payload.get("timeout") or 3600),
            project_type=payload.get("project_type") or "code",
        )

        if getattr(result, "success", False):
            logger.info(
                f"补派成功: run_id={run_id} attempts={payload.get('attempts')} "
                f"worker={getattr(result, 'worker_name', '')}"
            )
            return True

        # 仍失败 → 再入队或放弃(内部会先 enqueue 新 payload 再返回,
        # 外层 ack 旧 raw_payload,顺序保证不丢单)
        await self._reenqueue_or_give_up(
            payload,
            reason=getattr(result, "error", "") or "dispatch failed",
        )
        return True

    async def _reenqueue_or_give_up(self, payload: dict[str, Any], *, reason: str) -> None:
        attempts = int(payload.get("attempts") or 0) + 1
        enqueued = await redispatch_service.enqueue(
            run_id=payload.get("run_id") or "",
            task_id=payload.get("task_id"),
            project_id=payload.get("project_id") or "",
            params=payload.get("params") or {},
            environment_vars=payload.get("environment_vars") or None,
            runtime_env_name=payload.get("runtime_env_name") or None,
            timeout=int(payload.get("timeout") or 3600),
            project_type=payload.get("project_type") or "code",
            attempts=attempts,
            reason=reason,
        )
        if not enqueued:
            # 超阈值放弃：置 FAILED + audit + 告警
            await self._give_up(payload, reason=reason)

    async def _give_up(self, payload: dict[str, Any], *, reason: str) -> None:
        run_id = payload.get("run_id") or ""
        attempts = int(payload.get("attempts") or 0)
        if not await self._mark_failed(run_id, attempts, reason):
            logger.info(f"补派放弃项已由其他路径终结，跳过重复副作用: run_id={run_id}")
            return

        # audit_log
        try:
            from antcode_core.domain.models.audit_log import AuditLog

            await AuditLog.create(
                action="redispatch_gave_up",
                resource_type="task_run",
                resource_id=run_id,
                details={
                    "attempts": attempts,
                    "reason": reason,
                    "project_id": payload.get("project_id"),
                },
            )
        except Exception as exc:
            logger.warning(f"补派放弃写 audit 失败 run_id={run_id}: {exc}")

        # 告警
        try:
            from antcode_core.application.services.alert import alert_service

            await alert_service.send_alert(
                message=(
                    f"派发补派耗尽: run_id={run_id} project_id={payload.get('project_id')} "
                    f"attempts={attempts} 最后错误: {reason}"
                ),
                level="ERROR",
                source="redispatch",
            )
        except Exception as exc:
            logger.debug(f"补派放弃告警发送失败（可忽略）: {exc}")

        logger.error(f"补派放弃: run_id={run_id} attempts={attempts} reason={reason!r}")

    @staticmethod
    async def _mark_failed(run_id: str, attempts: int, reason: str) -> bool:
        from antcode_core.application.services.scheduler.execution_status_service import (
            execution_status_service,
        )

        updated = await execution_status_service.update_dispatch_status(
            run_id=run_id,
            status=DispatchStatus.FAILED,
            status_at=datetime.now(UTC),
            error_message=f"补派耗尽 ({attempts}次): {reason}"[:1000],
        )
        if updated:
            return True
        current = await TaskRun.get_or_none(run_id=run_id)
        if current is not None and current.dispatch_status in {
            DispatchStatus.ACKED,
            DispatchStatus.REJECTED,
            DispatchStatus.TIMEOUT,
            DispatchStatus.FAILED,
        }:
            return False
        raise RuntimeError(f"补派耗尽状态未持久化: run_id={run_id}")


redispatch_loop = RedispatchLoop()


__all__ = ["RedispatchLoop", "redispatch_loop"]
