"""Failure handling for tasks polled before scheduler handoff."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from antcode_worker.transport.base import GenerationLostError


async def handle_poll_failure(
    *,
    engine: Any,
    error: Exception,
    receipt: str | None,
    admitted_run_id: str | None,
) -> bool:
    """Return true when polling must stop after a generation loss."""
    if isinstance(error, GenerationLostError):
        logger.error("Lease generation 已被新代际取代，立即中止本进程全部执行")
        await engine._abort_for_ownership_failure("lease generation superseded")
        return True
    if receipt:
        try:
            await _recover_uncommitted_delivery(
                engine=engine,
                receipt=receipt,
                admitted_run_id=admitted_run_id,
            )
        except Exception:
            logger.exception("未交接任务的精确 PEL 恢复登记失败")
    if engine._flow_controller:
        engine._flow_controller.on_failure()
    logger.exception("轮询异常")
    await asyncio.sleep(1)
    return False


async def _recover_uncommitted_delivery(
    *,
    engine: Any,
    receipt: str,
    admitted_run_id: str | None,
) -> None:
    release_error: Exception | None = None
    if admitted_run_id:
        try:
            await engine._release_run_ownership(admitted_run_id)
        except Exception as exc:
            release_error = exc
        await engine._state_manager.remove(admitted_run_id)
    deferred = await engine._transport.defer_task(
        receipt,
        reason="poll delivery failed before scheduler handoff",
    )
    if not deferred:
        raise RuntimeError(f"未交接任务 defer 失败: receipt={receipt}")
    if release_error:
        raise RuntimeError(f"未交接任务 ownership release 失败: run_id={admitted_run_id}") from release_error
