"""批次管理服务

实现爬取批次的创建、暂停、恢复、取消和状态查询功能。

需求: 1.1, 1.2, 1.3, 1.4, 1.5
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loguru import logger

from antcode_core.application.services.base import BaseService
from antcode_core.application.services.crawl.dedup_service import CrawlDedupService, crawl_dedup_service
from antcode_core.application.services.crawl.progress_service import (
    CrawlProgressService,
    crawl_progress_service,
)
from antcode_core.application.services.crawl.queue_service import CrawlQueueService, crawl_queue_service
from antcode_core.common.config import settings
from antcode_core.common.exceptions import BatchNotFoundError, BatchStateError
from antcode_core.domain.models.crawl import BatchStatus, CrawlBatch
from antcode_core.domain.models.project import Project

# 批次状态转换规则
# 定义每个状态可以转换到哪些状态
VALID_STATE_TRANSITIONS = {
    BatchStatus.PENDING: [BatchStatus.RUNNING, BatchStatus.CANCELLED],
    BatchStatus.RUNNING: [BatchStatus.PAUSED, BatchStatus.COMPLETED, BatchStatus.FAILED, BatchStatus.CANCELLED],
    BatchStatus.PAUSED: [BatchStatus.RUNNING, BatchStatus.CANCELLED],
    BatchStatus.COMPLETED: [],  # 终态，不能转换
    BatchStatus.FAILED: [],  # 终态，不能转换
    BatchStatus.CANCELLED: [],  # 终态，不能转换
}


class CrawlBatchService(BaseService):
    """批次管理服务

    实现爬取批次的完整生命周期管理，包括：
    - 创建批次
    - 暂停批次
    - 恢复批次
    - 取消批次
    - 状态查询

    需求: 1.1, 1.2, 1.3, 1.4, 1.5
    """

    def __init__(
        self,
        queue_service: CrawlQueueService = None,
        progress_service: CrawlProgressService = None,
        dedup_service: CrawlDedupService = None,
    ):
        """初始化批次管理服务

        Args:
            queue_service: 队列服务，为 None 时使用全局实例
            progress_service: 进度服务，为 None 时使用全局实例
            dedup_service: 去重服务，为 None 时使用全局实例
        """
        super().__init__()
        self._queue_service = queue_service or crawl_queue_service
        self._progress_service = progress_service or crawl_progress_service
        self._dedup_service = dedup_service or crawl_dedup_service
        self._event_client = None

    # P1-14 (审查报告): 事件发送失败旧版直接 warn 吞掉，DB 已改但 Master 端
    # 永远收不到事件；批次要么永久 PENDING/RUNNING，要么状态与实际漂移。
    # 简化版 outbox：xadd 失败入 Redis ZSet 重试队列，下次发送 opportunistic
    # 顺带 flush 到期条目。ZSet key: ``{namespace}:crawl:batch:events:retry``
    # member = JSON payload，score = 到期时间戳（ms）。
    _RETRY_MAX_ATTEMPTS = 5
    _RETRY_BASE_DELAY_SEC = 30
    _RETRY_MAX_DELAY_SEC = 300

    def _retry_queue_key(self) -> str:
        ns = settings.REDIS_NAMESPACE or "antcode"
        return f"{ns}:crawl:batch:events:retry"

    @classmethod
    def _next_retry_delay(cls, attempts: int) -> int:
        """指数退避 base * 2^attempts capped 到 max，30s..300s。"""
        if attempts <= 0:
            return cls._RETRY_BASE_DELAY_SEC
        delay = cls._RETRY_BASE_DELAY_SEC * (2 ** min(attempts, 10))
        return min(delay, cls._RETRY_MAX_DELAY_SEC)

    async def _enqueue_event_retry(
        self, event: str, batch_id: str, attempts: int = 0, reason: str = ""
    ) -> bool:
        """把发送失败的事件挂入 Redis ZSet 重试队列。

        返回 True 表示已入队；False 表示超阈值或队列不可用。
        """
        import json
        import time

        if attempts >= self._RETRY_MAX_ATTEMPTS:
            logger.error(
                f"批次事件 retry 超上限 {self._RETRY_MAX_ATTEMPTS} 丢弃: "
                f"event={event} batch_id={batch_id} reason={reason!r}"
            )
            return False
        try:
            from antcode_core.infrastructure.redis.client import get_redis_client

            redis = await get_redis_client()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                f"批次事件 retry 入队失败(Redis 不可用): {event}({batch_id}) err={exc}"
            )
            return False
        payload = json.dumps(
            {
                "event": event,
                "batch_id": batch_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "attempts": int(attempts),
                "reason": reason,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        delay = self._next_retry_delay(attempts)
        score = int(time.time() * 1000) + delay * 1000
        try:
            await redis.zadd(self._retry_queue_key(), {payload: score})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"批次事件 retry ZADD 失败: {event}({batch_id}) err={exc}")
            return False
        logger.warning(
            f"批次事件入 retry queue: event={event} batch_id={batch_id} "
            f"attempts={attempts + 1}/{self._RETRY_MAX_ATTEMPTS} delay={delay}s"
        )
        return True

    async def _flush_pending_event_retries(self, limit: int = 20) -> int:
        """拉取到期的 retry 事件，重发到 stream；失败重新入队递增 attempts。

        每次 xadd 成功后 opportunistically 调用，保证 flush 与批次生命周期共
        享同一进程。返回本次成功发送的条数。
        """
        import json
        import time

        if not settings.REDIS_ENABLED or self._event_client is None:
            return 0
        try:
            from antcode_core.infrastructure.redis.client import get_redis_client

            redis = await get_redis_client()
        except Exception:  # noqa: BLE001
            return 0
        key = self._retry_queue_key()
        now_ms = int(time.time() * 1000)
        try:
            members = await redis.zrangebyscore(
                key, min=0, max=now_ms, start=0, num=limit
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"flush retry queue ZRANGEBYSCORE 失败: {exc}")
            return 0
        if not members:
            return 0
        try:
            await redis.zrem(key, *members)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"flush retry queue ZREM 失败: {exc}")
            return 0

        sent = 0
        for raw in members:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="ignore")
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError) as exc:
                logger.debug(f"retry payload 反序列化失败,丢弃: {exc}")
                continue
            event = payload.get("event")
            batch_id = payload.get("batch_id")
            attempts = int(payload.get("attempts", 0))
            if not event or not batch_id:
                continue
            try:
                await self._event_client.xadd(
                    settings.scheduler_event_stream,
                    {
                        "event": event,
                        "batch_id": batch_id,
                        "timestamp": payload.get(
                            "timestamp", datetime.now(UTC).isoformat()
                        ),
                    },
                    maxlen=settings.SCHEDULER_EVENT_MAXLEN,
                )
                sent += 1
                logger.info(f"批次事件 retry 成功: {event}({batch_id})")
            except Exception as exc:  # noqa: BLE001
                await self._enqueue_event_retry(
                    event, batch_id, attempts=attempts + 1, reason=str(exc)
                )
        return sent

    async def _publish_batch_event(self, event: str, batch_id: str) -> None:
        """发布批次生命周期事件到调度事件流

        让 Master 端的控制平面接管批次的实际任务分发逻辑（暂停/恢复/取消等）。

        P1-14: xadd 失败不再吞异常；改为入 Redis ZSet 重试队列，后续 publish
        调用会 opportunistically drain。若 Redis 完全不可用（连 retry 也入不
        了队），抛出让调用方决定；DB 状态已改但事件真丢时至少要 crash 醒目。

        Args:
            event: 事件名，如 batch_started / batch_paused / batch_resumed / batch_cancelled
            batch_id: 批次公开 ID
        """
        if not settings.REDIS_ENABLED:
            logger.debug(f"Redis 未启用，跳过批次事件发布: {event}({batch_id})")
            return
        if self._event_client is None:
            from antcode_core.infrastructure.redis.streams import StreamClient

            self._event_client = StreamClient()

        # 先 opportunistic flush 一批到期的 retry，尽早追上进度
        try:
            await self._flush_pending_event_retries()
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"flush retry queue 忽略异常: {exc}")

        try:
            await self._event_client.xadd(
                settings.scheduler_event_stream,
                {
                    "event": event,
                    "batch_id": batch_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                maxlen=settings.SCHEDULER_EVENT_MAXLEN,
            )
            logger.info(f"已发布批次事件: {event}({batch_id})")
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"发布批次事件失败，入 retry queue: {event}({batch_id}): {e}"
            )
            enqueued = await self._enqueue_event_retry(
                event, batch_id, attempts=0, reason=str(e)
            )
            if not enqueued:
                # retry 也入不了队（Redis 彻底挂了 or 超阈值），显式抛错让调
                # 用方感知——总比默默把 DB 改了事件没发要好。
                raise

    # =========================================================================
    # 批次创建
    # =========================================================================

    async def create_batch(
        self,
        project_id: str,
        name: str,
        seed_urls: list,
        user_id: int,
        description: str = None,
        max_depth: int = 3,
        max_pages: int = 10000,
        max_concurrency: int = 50,
        request_delay: float = 0.5,
        timeout: int = 30,
        max_retries: int = 3,
        is_test: bool = False,
    ) -> CrawlBatch:
        """创建爬取批次

        Args:
            project_id: 项目公开 ID
            name: 批次名称
            seed_urls: 种子 URL 列表
            user_id: 创建者 ID
            description: 批次描述
            max_depth: 最大爬取深度
            max_pages: 最大爬取页面数
            max_concurrency: 最大并发数
            request_delay: 请求间隔（秒）
            timeout: 请求超时（秒）
            max_retries: 最大重试次数
            is_test: 是否为测试批次

        Returns:
            创建的 CrawlBatch 对象

        需求: 1.1 - 用户提交创建批次请求时创建新批次并返回批次 ID
        """
        # 获取项目内部 ID
        project = await self._get_project_by_public_id(project_id)
        if not project:
            raise ValueError(f"项目 {project_id} 不存在")

        # 创建批次记录
        batch = await CrawlBatch.create(
            project_id=project.id,
            name=name,
            description=description,
            seed_urls=seed_urls,
            max_depth=max_depth,
            max_pages=max_pages,
            max_concurrency=max_concurrency,
            request_delay=request_delay,
            timeout=timeout,
            max_retries=max_retries,
            status=BatchStatus.PENDING,
            is_test=is_test,
            user_id=user_id,
        )

        logger.info(f"创建批次: batch_id={batch.public_id}, project={project_id}, "
                    f"name={name}, seed_urls={len(seed_urls)}, is_test={is_test}")

        return batch

    async def start_batch(self, batch_id: str) -> CrawlBatch:
        """启动批次执行

        将批次状态从 PENDING 转换为 RUNNING，并初始化队列和进度。

        Args:
            batch_id: 批次公开 ID

        Returns:
            更新后的 CrawlBatch 对象
        """
        batch = await self._get_batch_by_public_id(batch_id)
        if not batch:
            raise BatchNotFoundError(batch_id)

        # 检查状态转换是否有效
        self._validate_state_transition(batch, BatchStatus.RUNNING)

        # 获取项目公开 ID
        project = await Project.filter(id=batch.project_id).first()
        project_public_id = project.public_id if project else str(batch.project_id)

        # 确保队列存在
        await self._queue_service.ensure_queues(project_public_id)

        # 初始化进度
        await self._progress_service.init_progress(
            project_id=project_public_id,
            batch_id=batch.public_id,
            total_urls=len(batch.seed_urls),
        )

        # 将种子 URL 入队
        result = await self._queue_service.enqueue_urls(
            project_id=project_public_id,
            urls=batch.seed_urls,
            batch_id=batch.public_id,
            skip_dedup=True,  # 种子 URL 不去重
        )

        # 更新批次状态
        batch.status = BatchStatus.RUNNING
        batch.started_at = datetime.now()
        await batch.save()

        logger.info(f"启动批次: batch_id={batch_id}, enqueued={result.enqueued}")

        # 通知 Master 控制平面接管批次任务分发
        await self._publish_batch_event("batch_started", batch.public_id)

        return batch

    # =========================================================================
    # 批次暂停
    # =========================================================================

    async def pause_batch(self, batch_id: str) -> CrawlBatch:
        """暂停批次

        停止分发新任务并保持当前进度。已分发的任务会继续执行。

        Args:
            batch_id: 批次公开 ID

        Returns:
            更新后的 CrawlBatch 对象

        需求: 1.2 - 用户请求暂停批次时停止分发新任务并保持当前进度
        """
        batch = await self._get_batch_by_public_id(batch_id)
        if not batch:
            raise BatchNotFoundError(batch_id)

        # 检查状态转换是否有效
        self._validate_state_transition(batch, BatchStatus.PAUSED)

        # 获取项目公开 ID
        project = await Project.filter(id=batch.project_id).first()
        project_public_id = project.public_id if project else str(batch.project_id)

        # 保存检查点
        await self._progress_service.save_checkpoint(
            project_id=project_public_id,
            batch_id=batch.public_id,
        )

        # 更新批次状态
        batch.status = BatchStatus.PAUSED
        await batch.save()

        logger.info(f"暂停批次: batch_id={batch_id}")

        # 通知 Master 停止分发新任务
        await self._publish_batch_event("batch_paused", batch.public_id)

        return batch

    # =========================================================================
    # 批次恢复
    # =========================================================================

    async def resume_batch(self, batch_id: str) -> CrawlBatch:
        """恢复批次

        从暂停点继续分发任务。

        Args:
            batch_id: 批次公开 ID

        Returns:
            更新后的 CrawlBatch 对象

        需求: 1.3 - 用户请求恢复批次时从暂停点继续分发任务
        """
        batch = await self._get_batch_by_public_id(batch_id)
        if not batch:
            raise BatchNotFoundError(batch_id)

        # 检查状态转换是否有效
        self._validate_state_transition(batch, BatchStatus.RUNNING)

        # 获取项目公开 ID
        project = await Project.filter(id=batch.project_id).first()
        project_public_id = project.public_id if project else str(batch.project_id)

        # 从检查点恢复进度
        await self._progress_service.restore_from_checkpoint(
            project_id=project_public_id,
            batch_id=batch.public_id,
        )

        # 更新批次状态
        batch.status = BatchStatus.RUNNING
        await batch.save()

        logger.info(f"恢复批次: batch_id={batch_id}")

        # 通知 Master 恢复批次任务分发
        await self._publish_batch_event("batch_resumed", batch.public_id)

        return batch

    # =========================================================================
    # 批次取消
    # =========================================================================

    async def cancel_batch(
        self,
        batch_id: str,
        cleanup: bool = True,
        project_public_id: str | None = None,
    ) -> CrawlBatch:
        """取消批次

        停止所有任务并清理相关资源。

        Args:
            batch_id: 批次公开 ID
            cleanup: 是否清理队列和进度数据

        Returns:
            更新后的 CrawlBatch 对象

        需求: 1.4 - 用户请求取消批次时停止所有任务并清理相关资源
        """
        batch = await self._get_batch_by_public_id(batch_id)
        if not batch:
            raise BatchNotFoundError(batch_id)

        if project_public_id is None:
            project_public_id = await self._get_project_public_id(batch.project_id)

        return await self._cancel_batch_instance(
            batch=batch,
            cleanup=cleanup,
            project_public_id=project_public_id,
        )

    # =========================================================================
    # 批次完成
    # =========================================================================

    async def complete_batch(self, batch_id: str, success: bool = True) -> CrawlBatch:
        """标记批次完成

        Args:
            batch_id: 批次公开 ID
            success: 是否成功完成

        Returns:
            更新后的 CrawlBatch 对象

        **P1-32 修复**：``batch.save()`` 只带主键做 UPDATE，同一批次并发被 loop
        （见 ``crawl_batch_status_loop``）或 API 其他调用改成 PAUSED/CANCELLED
        时，会把用户已经生效的暂停/取消状态静默覆盖成 COMPLETED/FAILED。改用
        条件 UPDATE + 允许的 from-status 白名单，落地失败时抛 BatchStateError
        让调用方感知竞态。
        """
        batch = await self._get_batch_by_public_id(batch_id)
        if not batch:
            raise BatchNotFoundError(batch_id)

        target_status = BatchStatus.COMPLETED if success else BatchStatus.FAILED

        # 检查状态转换是否有效（in-memory，防呆；下面 CAS 才是权威守卫）
        self._validate_state_transition(batch, target_status)

        # 获取项目公开 ID
        project = await Project.filter(id=batch.project_id).first()
        project_public_id = project.public_id if project else str(batch.project_id)

        # 保存最终检查点
        await self._progress_service.save_checkpoint(
            project_id=project_public_id,
            batch_id=batch.public_id,
        )

        # P1-32: 条件 UPDATE。允许 from-status 集合 = ``{RUNNING}``；
        # PAUSED/PENDING → COMPLETED/FAILED 语义上不成立（暂停未运行的批次不
        # 该被 auto-complete），其他终态是幂等无效转移。这里跟 loop 里 CAS
        # 保持同一 from-status 白名单，两端才不会互相打架。
        now = datetime.now()
        updated = await CrawlBatch.filter(
            id=batch.id,
            status=BatchStatus.RUNNING.value,
        ).update(status=target_status, completed_at=now)
        if not updated:
            # 竞态 or 期望状态不匹配：重新读取当前状态告诉调用方
            fresh = await CrawlBatch.filter(id=batch.id).only("status").first()
            current = fresh.status if fresh else "unknown"
            logger.warning(
                f"complete_batch CAS 失败: batch_id={batch_id} "
                f"expected=running actual={current} target={target_status}"
            )
            raise BatchStateError(
                batch_id=batch.public_id,
                current_state=str(current),
                expected_states=[BatchStatus.RUNNING.value],
            )
        # 同步内存对象
        batch.status = target_status
        batch.completed_at = now

        logger.info(f"批次完成: batch_id={batch_id}, status={target_status}")

        return batch

    # =========================================================================
    # 状态查询
    # =========================================================================

    async def get_batch(self, batch_id: str) -> CrawlBatch | None:
        """获取批次详情

        Args:
            batch_id: 批次公开 ID

        Returns:
            CrawlBatch 对象，不存在时返回 None

        需求: 1.5 - 用户查询批次状态时返回批次的当前状态和进度信息
        """
        return await self._get_batch_by_public_id(batch_id)

    async def get_batch_status(self, batch_id: str) -> dict:
        """获取批次状态和进度信息

        Args:
            batch_id: 批次公开 ID

        Returns:
            包含状态和进度的字典

        需求: 1.5 - 用户查询批次状态时返回批次的当前状态和进度信息
        """
        batch = await self._get_batch_by_public_id(batch_id)
        if not batch:
            raise BatchNotFoundError(batch_id)

        # 获取项目公开 ID
        project = await Project.filter(id=batch.project_id).first()
        project_public_id = project.public_id if project else str(batch.project_id)

        # 获取进度信息
        progress = await self._progress_service.get_progress(
            project_id=project_public_id,
            batch_id=batch.public_id,
        )

        result = {
            "batch_id": batch.public_id,
            "project_id": project_public_id,
            "name": batch.name,
            "status": batch.status,
            "is_test": batch.is_test,
            "created_at": batch.created_at.isoformat() if batch.created_at else None,
            "started_at": batch.started_at.isoformat() if batch.started_at else None,
            "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
            "config": {
                "max_depth": batch.max_depth,
                "max_pages": batch.max_pages,
                "max_concurrency": batch.max_concurrency,
                "request_delay": batch.request_delay,
                "timeout": batch.timeout,
                "max_retries": batch.max_retries,
            },
        }

        if progress:
            result["progress"] = progress.to_dict()
        else:
            result["progress"] = None

        return result

    async def list_batches(
        self,
        project_id: str = None,
        user_id: int = None,
        status: str = None,
        is_test: bool = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple:
        """列出批次

        Args:
            project_id: 项目公开 ID（可选）
            user_id: 用户 ID（可选）
            status: 状态过滤（可选）
            is_test: 是否测试批次（可选）
            page: 页码
            size: 每页数量

        Returns:
            (batches, total) 元组
        """
        query = CrawlBatch.all()

        # 项目过滤
        if project_id:
            project = await self._get_project_by_public_id(project_id)
            if project:
                query = query.filter(project_id=project.id)
            else:
                return [], 0

        # 用户过滤
        if user_id:
            query = query.filter(user_id=user_id)

        # 状态过滤
        if status:
            query = query.filter(status=status)

        # 测试批次过滤
        if is_test is not None:
            query = query.filter(is_test=is_test)

        # 分页查询
        batches, total = await self.query.paginate(
            query, page, size, order_by="-created_at"
        )

        return batches, total

    # =========================================================================
    # 辅助方法
    # =========================================================================

    async def _get_batch_by_public_id(self, batch_id: str) -> CrawlBatch | None:
        """通过公开 ID 获取批次

        Args:
            batch_id: 批次公开 ID

        Returns:
            CrawlBatch 对象，不存在时返回 None
        """
        return await CrawlBatch.filter(public_id=batch_id).first()

    async def _get_project_by_public_id(self, project_id: str) -> Project | None:
        """通过公开 ID 获取项目

        Args:
            project_id: 项目公开 ID

        Returns:
            Project 对象，不存在时返回 None
        """
        return await Project.filter(public_id=project_id).first()

    def _validate_state_transition(self, batch: CrawlBatch, target_status: str):
        """验证状态转换是否有效

        Args:
            batch: 批次对象
            target_status: 目标状态

        Raises:
            BatchStateError: 状态转换无效时抛出

        需求: 1.2, 1.3, 1.4 - 状态转换需遵循有效的状态机规则
        """
        current_status = batch.status
        valid_targets = VALID_STATE_TRANSITIONS.get(current_status, [])

        if target_status not in valid_targets:
            raise BatchStateError(
                batch_id=batch.public_id,
                current_state=current_status,
                expected_states=valid_targets,
            )

    def is_valid_transition(self, current_status: str, target_status: str) -> bool:
        """检查状态转换是否有效

        Args:
            current_status: 当前状态
            target_status: 目标状态

        Returns:
            是否有效
        """
        valid_targets = VALID_STATE_TRANSITIONS.get(current_status, [])
        return target_status in valid_targets

    # =========================================================================
    # 批量操作
    # =========================================================================

    async def cancel_all_running_batches(self, project_id: str = None) -> int:
        """取消所有运行中的批次

        Args:
            project_id: 项目公开 ID（可选，不指定则取消所有项目的）

        Returns:
            取消的批次数量
        """
        query = CrawlBatch.filter(status=BatchStatus.RUNNING)

        if project_id:
            project = await self._get_project_by_public_id(project_id)
            if project:
                query = query.filter(project_id=project.id)
            else:
                return 0

        batches = await query.all()
        cancelled_count = 0

        project_public_id_map = {}
        project_ids = list({b.project_id for b in batches})
        if project_ids:
            project_public_id_map = await self.query.batch_get_project_public_ids(project_ids)

        for batch in batches:
            try:
                project_public_id = project_public_id_map.get(
                    batch.project_id, str(batch.project_id)
                )
                await self._cancel_batch_instance(
                    batch=batch,
                    cleanup=True,
                    project_public_id=project_public_id,
                )
                cancelled_count += 1
            except Exception as e:
                logger.error(f"取消批次失败: batch_id={batch.public_id}, 错误: {e}")

        logger.info(f"批量取消批次: project={project_id}, cancelled={cancelled_count}")

        return cancelled_count

    async def cleanup_test_batches(self, project_id: str = None, days: int = 7) -> int:
        """清理过期的测试批次

        Args:
            project_id: 项目公开 ID（可选）
            days: 保留天数

        Returns:
            清理的批次数量
        """
        cutoff = datetime.now() - timedelta(days=days)

        query = CrawlBatch.filter(
            is_test=True,
            created_at__lt=cutoff,
        )

        if project_id:
            project = await self._get_project_by_public_id(project_id)
            if project:
                query = query.filter(project_id=project.id)
            else:
                return 0

        batches = await query.all()
        deleted_count = 0

        project_public_id_map = {}
        project_ids = list({b.project_id for b in batches})
        if project_ids:
            project_public_id_map = await self.query.batch_get_project_public_ids(project_ids)

        for batch in batches:
            try:
                project_public_id = project_public_id_map.get(
                    batch.project_id, str(batch.project_id)
                )

                # 清理进度数据
                await self._progress_service.clear_progress(
                    project_id=project_public_id,
                    batch_id=batch.public_id,
                )

                # 删除批次记录
                await batch.delete()
                deleted_count += 1

            except Exception as e:
                logger.error(f"清理测试批次失败: batch_id={batch.public_id}, 错误: {e}")

        logger.info(f"清理测试批次: project={project_id}, deleted={deleted_count}")

        return deleted_count

    async def _get_project_public_id(self, project_id: int) -> str:
        project = await Project.filter(id=project_id).only("public_id").first()
        return project.public_id if project else str(project_id)

    async def _cancel_batch_instance(
        self,
        batch: CrawlBatch,
        cleanup: bool,
        project_public_id: str,
    ) -> CrawlBatch:
        # 检查状态转换是否有效
        self._validate_state_transition(batch, BatchStatus.CANCELLED)

        if cleanup:
            await self._queue_service.clear_queues(project_public_id)
            await self._progress_service.clear_progress(
                project_id=project_public_id,
                batch_id=batch.public_id,
            )
            await self._dedup_service.clear(project_public_id)

        batch.status = BatchStatus.CANCELLED
        batch.completed_at = datetime.now()
        await batch.save()

        logger.info(f"取消批次: batch_id={batch.public_id}, cleanup={cleanup}")

        # 通知 Master 停止所有相关任务分发
        await self._publish_batch_event("batch_cancelled", batch.public_id)

        return batch


# 全局服务实例
crawl_batch_service = CrawlBatchService()
