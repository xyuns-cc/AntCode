"""Crawl 告警周期评估 loop。

L4: master 周期扫描活跃 CrawlBatch 关联的 project，触发
``crawl_metrics_service.check_alerts(project_id)``。命中阈值时
``_log_alert`` 已被 L4 修改为同步投递到 ``alert_service.send_alert``，
经 feishu/webhook 等通道分发。

设计：
- Leader-gated（与 log_ingest_loop 一致的自愈式 gate）。
- 一次 tick 只扫最多 ``MAX_PROJECTS_PER_TICK`` 个 project，避免大规模部署
  时同 tick 把 alert_service 打爆。
- 未来接 SystemConfig(category="alert") 的阈值刷新，一期先用硬编码 default。
"""

from __future__ import annotations

import asyncio

from antcode_core.application.services.crawl.metrics_service import (
    crawl_metrics_service,
)
from antcode_core.domain.models import CrawlBatch
from antcode_core.domain.models.enums import BatchStatus
from loguru import logger

from antcode_master.leader import ensure_leader

MAX_PROJECTS_PER_TICK = 500


class AlertCheckLoop:
    def __init__(self, poll_interval_seconds: float = 60.0) -> None:
        self._poll_interval = poll_interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"crawl 告警评估 loop 已启动: interval={self._poll_interval}s")

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
        logger.info("crawl 告警评估 loop 已停止")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                # P2-21: 多副本 master 部署时,`ensure_leader()` 走 Redis
                # 权威校验(见 `leader.ensure_leader`),只有 Leader 副本会
                # 进入 `_tick()` 拉 CrawlBatch → check_alerts → send_alert。
                # Follower 副本恒短路 continue,不会出现 N 个副本各评估一遍
                # 同一 project 导致同一条告警被 fanout N 次的情况。
                if not await ensure_leader():
                    await asyncio.sleep(self._poll_interval)
                    continue
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"crawl 告警评估 loop 异常: {exc}")
            await asyncio.sleep(self._poll_interval)

    async def _tick(self) -> None:
        # R1-P0-6 (审查报告): 老实现 `pid = str(batch.project_id)` 用的是
        # 内部 BigInt project id，而队列/去重/进度 key 都是用 project.public_id
        # 写入 —— rule:{public_id}:... vs rule:123:...，永不相交，告警恒为 0。
        # 修复：把 batch.project_id 解析为 project.public_id 再传给 check_alerts。
        from antcode_core.domain.models.project import Project

        active_statuses = [BatchStatus.RUNNING.value, BatchStatus.PAUSED.value]
        batches = await CrawlBatch.filter(status__in=active_statuses).all()

        # 一次性把涉及的 project 内部 id → public_id 拉出来
        internal_ids = list({b.project_id for b in batches if b.project_id})
        pid_map: dict[int, str] = {}
        if internal_ids:
            for p in await Project.filter(id__in=internal_ids).only("id", "public_id").all():
                pid_map[p.id] = p.public_id

        seen_projects: set[str] = set()
        checked = 0
        for batch in batches:
            if checked >= MAX_PROJECTS_PER_TICK:
                break
            public_id = pid_map.get(batch.project_id)
            if not public_id:
                continue
            if public_id in seen_projects:
                continue
            seen_projects.add(public_id)
            checked += 1
            try:
                await crawl_metrics_service.check_alerts(public_id)
            except Exception as exc:
                logger.warning(f"check_alerts 失败: project={public_id} err={exc}")


alert_check_loop = AlertCheckLoop()
