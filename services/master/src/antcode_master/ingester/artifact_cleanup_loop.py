"""Leader-gated wrapper around ArtifactCleanupService."""

from __future__ import annotations

import asyncio
import contextlib

from antcode_core.application.services.projects.artifact_cleanup_service import (
    artifact_cleanup_service,
)
from antcode_core.domain.models.task_run import TASK_ID_ABSENT
from loguru import logger

from antcode_master.leader import ensure_leader


class ArtifactCleanupLoop:
    """Runs artifact cleanup exclusively on the leader Master."""

    def __init__(
        self,
        interval_hours: int = 24,
        leader_poll_interval: float = 30.0,
    ) -> None:
        self._interval_hours = interval_hours
        self._leader_poll_interval = leader_poll_interval
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("artifact 清理循环已启动: 间隔={}h", self._interval_hours)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("artifact 清理循环已停止")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                if not await ensure_leader():
                    await asyncio.sleep(self._leader_poll_interval)
                    continue
                await self._tick()
                await asyncio.sleep(self._interval_hours * 3600)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("artifact 清理循环异常: {}", exc)
                await asyncio.sleep(self._leader_poll_interval)

    async def _tick(self) -> None:
        await artifact_cleanup_service.cleanup_now()
        # 迁移删除 FK 后顺便检查孤儿，只记录、不自动删除。
        await self._check_orphans()

    async def _check_orphans(self) -> None:
        """T7-P2-8: 孤儿 task_executions 周检。

        FK 已在迁移 26 删除，仅靠应用层 owner 校验保证参照完整；一旦有
        drift（比如 scheduled_tasks 手动 DELETE），task_executions 会残留。
        本方法只打日志 + 指标，不做级联删除——避免误伤运维手工 archive
        场景。检出孤儿计数 > 0 时提醒运维介入。
        """
        try:
            from tortoise import connections

            conn = connections.get("default")
            # 爬取批次 run 的 task_id 是 TASK_ID_ABSENT 哨兵，本就没有 scheduled_tasks 行，
            # 不排除它会把每条批次 run 都报成孤儿，让这个告警永久为真、失去指示意义。
            _, rows = await conn.execute_query(
                'SELECT COUNT(*) AS n FROM "task_executions" te '
                "WHERE te.task_id IS NOT NULL AND te.task_id <> $1 "
                'AND NOT EXISTS (SELECT 1 FROM "scheduled_tasks" st WHERE st.id = te.task_id)',
                [TASK_ID_ABSENT],
            )
            n = int(rows[0]["n"]) if rows else 0
            if n > 0:
                logger.warning(
                    f"[orphan-check] 检出 {n} 条孤儿 task_executions "
                    "(task_id 指向已不存在的 scheduled_tasks，请人工确认)"
                )
            else:
                logger.debug("[orphan-check] 无孤儿 task_executions")
        except Exception as exc:
            logger.warning(f"[orphan-check] 查询失败: {exc}")


artifact_cleanup_loop = ArtifactCleanupLoop()

__all__ = ["ArtifactCleanupLoop", "artifact_cleanup_loop"]
