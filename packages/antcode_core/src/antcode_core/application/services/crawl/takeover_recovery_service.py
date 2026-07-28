"""Crawl Leader 接管恢复协调器。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Protocol

from loguru import logger

from antcode_core.application.services.crawl.backends import (
    CrawlQueueBackend,
    QueueTask,
    ReclaimedTask,
    get_queue_backend,
)
from antcode_core.domain.models.enums import BatchStatus

DEFAULT_RECOVERY_TIMEOUT_MS = 300_000
DEFAULT_RECOVERY_BATCH_SIZE = 100
DEFAULT_MAX_RECOVERY_RETRIES = 3

BatchIdLoader = Callable[[], Awaitable[list[str]]]
BatchRecoverer = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class TakeoverRecoveryConfig:
    timeout_ms: int = DEFAULT_RECOVERY_TIMEOUT_MS
    batch_size: int = DEFAULT_RECOVERY_BATCH_SIZE
    max_retries: int = DEFAULT_MAX_RECOVERY_RETRIES


@dataclass(frozen=True)
class TakeoverRecoveryReport:
    projects_scanned: int = 0
    tasks_requeued: int = 0
    tasks_dead_lettered: int = 0
    pending_not_timed_out: int = 0
    batches_recovered: int = 0
    failures: tuple[str, ...] = ()


class TakeoverRecoveryError(RuntimeError):
    """接管恢复未完整完成。

    C1: ``recover()`` 不再抛出本异常——恢复失败会记录到
    ``TakeoverRecoveryReport.failures`` 并留待后续重试，避免单个
    顽固失败的批次导致全集群无 Leader。类保留用于向后兼容。
    """

    def __init__(self, report: TakeoverRecoveryReport):
        self.report = report
        super().__init__("Leader 接管恢复失败: " + "; ".join(report.failures))


class TakeoverRecovery(Protocol):
    async def recover(self) -> TakeoverRecoveryReport: ...


@dataclass(frozen=True)
class _MoveReport:
    requeued: int = 0
    dead_lettered: int = 0
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ProjectReport:
    requeued: int = 0
    dead_lettered: int = 0
    pending_not_timed_out: int = 0
    failures: tuple[str, ...] = ()


class CrawlTakeoverRecoveryService:
    """恢复 Crawl PEL 与未完成批次调度。"""

    def __init__(
        self,
        backend: CrawlQueueBackend | None = None,
        *,
        config: TakeoverRecoveryConfig | None = None,
        batch_id_loader: BatchIdLoader | None = None,
        batch_recoverer: BatchRecoverer | None = None,
    ) -> None:
        self._backend = backend or get_queue_backend()
        self._config = config or TakeoverRecoveryConfig()
        self._batch_id_loader = batch_id_loader or _load_running_batch_ids
        self._batch_recoverer = batch_recoverer or _recover_batch_dispatch

    async def recover(self) -> TakeoverRecoveryReport:
        project_reports, queue_failures = await self._recover_queues()
        batches_recovered, batch_failures = await self._recover_batches()
        report = TakeoverRecoveryReport(
            projects_scanned=len(project_reports),
            tasks_requeued=sum(item.requeued for item in project_reports),
            tasks_dead_lettered=sum(item.dead_lettered for item in project_reports),
            pending_not_timed_out=sum(item.pending_not_timed_out for item in project_reports),
            batches_recovered=batches_recovered,
            failures=(*queue_failures, *batch_failures),
        )
        # C1: 恢复失败不再抛异常——否则一个顽固失败的批次会让新 Leader
        # 反复 acquire→recover 失败→resign，造成全集群无主。失败项逐条
        # 告警后留待后续重试（PEL reclaim / 下次接管仍会扫描到）。
        for failure in report.failures:
            logger.warning(f"Leader 接管恢复部分失败（留待后续重试）: {failure}")
        return report

    async def _recover_queues(self) -> tuple[list[_ProjectReport], tuple[str, ...]]:
        try:
            project_ids = await self._backend.list_project_ids()
        except Exception as exc:  # noqa: BLE001 - 汇总后显式抛出
            return [], (f"队列项目扫描失败: {exc}",)
        reports: list[_ProjectReport] = []
        failures: list[str] = []
        for project_id in project_ids:
            report = await self._recover_project(project_id)
            reports.append(report)
            failures.extend(report.failures)
        return reports, tuple(failures)

    async def _recover_project(self, project_id: str) -> _ProjectReport:
        requeued = 0
        dead_lettered = 0
        while True:
            try:
                before = await self._backend.get_pending_count(project_id)
                if before == 0:
                    return _ProjectReport(requeued, dead_lettered)
                items = await self._backend.reclaim(
                    project_id,
                    min_idle_ms=self._config.timeout_ms,
                    count=self._config.batch_size,
                )
            except Exception as exc:  # noqa: BLE001 - 保留其他项目恢复机会
                return _ProjectReport(requeued, dead_lettered, failures=(f"项目 {project_id} 扫描失败: {exc}",))
            if not items:
                return _ProjectReport(requeued, dead_lettered, pending_not_timed_out=before)
            moved = await self._move_items(project_id, items)
            requeued += moved.requeued
            dead_lettered += moved.dead_lettered
            if moved.failures:
                return _ProjectReport(requeued, dead_lettered, failures=moved.failures)
            progress_failure = await self._validate_progress(project_id, before)
            if progress_failure:
                return _ProjectReport(requeued, dead_lettered, failures=(progress_failure,))

    async def _move_items(self, project_id: str, items: list[ReclaimedTask]) -> _MoveReport:
        requeued = 0
        dead_lettered = 0
        failures: list[str] = []
        for item in items:
            try:
                moved, is_dead_letter = await self._move_item(project_id, item)
                requeued += int(moved and not is_dead_letter)
                dead_lettered += int(moved and is_dead_letter)
            except Exception as exc:  # noqa: BLE001 - 同批其他消息仍需恢复
                failures.append(f"项目 {project_id} 消息 {item.task.msg_id} 恢复失败: {exc}")
        return _MoveReport(requeued, dead_lettered, tuple(failures))

    async def _move_item(self, project_id: str, item: ReclaimedTask) -> tuple[bool, bool]:
        task = _task_with_cumulative_retries(item)
        if task.retry_count > self._config.max_retries:
            new_id = await self._backend.dead_letter_claimed(project_id, task)
            return new_id is not None, True
        new_id = await self._backend.requeue_claimed(project_id, task)
        return new_id is not None, False

    async def _validate_progress(self, project_id: str, before: int) -> str | None:
        after = await self._backend.get_pending_count(project_id)
        if after < before:
            return None
        return f"项目 {project_id} PEL 恢复无进展: before={before}, after={after}"

    async def _recover_batches(self) -> tuple[int, tuple[str, ...]]:
        try:
            batch_ids = await self._batch_id_loader()
        except Exception as exc:  # noqa: BLE001 - 队列报告仍需保留
            return 0, (f"运行中批次扫描失败: {exc}",)
        recovered = 0
        failures: list[str] = []
        for batch_id in batch_ids:
            try:
                await self._batch_recoverer(batch_id)
                recovered += 1
            except Exception as exc:  # noqa: BLE001 - 汇总所有失败
                failures.append(f"批次 {batch_id} 恢复失败: {exc}")
        return recovered, tuple(failures)


def _task_with_cumulative_retries(item: ReclaimedTask) -> QueueTask:
    """累计重试次数 = 载荷中已持久化的 retry_count + 本代 PEL delivery_count。

    C3: ``requeue_claimed`` 会 XADD 一条全新消息（PEL delivery_count 归零），
    如果用瞬态 delivery_count 覆盖载荷累计值，毒任务跨接管永远到不了
    死信。这里把累计值持久化回载荷，供下次接管继续累加并做死信判定。
    """
    return replace(item.task, retry_count=item.task.retry_count + item.delivery_count)


async def _load_running_batch_ids() -> list[str]:
    from antcode_core.domain.models import CrawlBatch

    rows = await CrawlBatch.filter(status=BatchStatus.RUNNING.value).only("public_id").all()
    return [row.public_id for row in rows]


async def _recover_batch_dispatch(batch_id: str) -> None:
    from antcode_core.application.services.crawl.batch_dispatcher_service import (
        crawl_batch_dispatcher_service,
    )

    await crawl_batch_dispatcher_service.handle_batch_event("batch_started", batch_id)
    logger.info(f"Leader 接管恢复 Crawl 批次调度: batch_id={batch_id}")
