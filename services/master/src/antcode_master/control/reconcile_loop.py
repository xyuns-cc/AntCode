"""
协调循环 (Reconcile Loop)

负责处理：
- 超时任务检测与恢复
- 状态不一致补偿
- 僵尸任务清理

注：失联 Worker 检测在 P3 已迁移到 ``LeaseSweeperLoop`` —
原来基于 ``last_heartbeat`` 阈值的判活逻辑被强一致 Lease 模型取代。

P1-#18 改造：原来 ``TaskRun.filter(...).all()`` 一把拉全表再
``for ... await task.save()``，单次 reconcile 在表稍大时 N×UPDATE
拖垮 DB。改为只 ``values("id")`` 取主键 + ``filter(id__in=...).update``
单次 bulk UPDATE。
"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from antcode_core.domain.models import TaskRun
from antcode_core.domain.models.enums import TaskStatus
from loguru import logger

from antcode_master.leader import ensure_leader, get_fencing_token


class ReconcileLoop:
    """协调循环"""

    def __init__(
        self,
        check_interval: int = 60,
        timeout_threshold: int = 300,
    ):
        """初始化协调循环

        Args:
            check_interval: 检查间隔（秒）
            timeout_threshold: 超时阈值（秒）
        """
        self.check_interval = check_interval
        self.timeout_threshold = timeout_threshold
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        """启动协调循环"""
        if self._running:
            logger.warning("协调循环已在运行")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            f"协调循环已启动: check_interval={self.check_interval}s, "
      f"timeout_threshold={self.timeout_threshold}s"
        )

    async def stop(self):
        """停止协调循环"""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        logger.info("协调循环已停止")

    async def _run_loop(self):
        """运行循环"""
        while self._running:
            try:
                # 只有 Leader 才执行协调
                if not await ensure_leader():
                    await asyncio.sleep(self.check_interval)
                    continue

                fencing_token = get_fencing_token()
                if fencing_token is None:
                    await asyncio.sleep(self.check_interval)
                    continue

                # 执行协调任务
                await self._reconcile(fencing_token)

                await asyncio.sleep(self.check_interval)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("协调循环异常")
                await asyncio.sleep(self.check_interval)

    async def _reconcile(self, fencing_token: int):
        """执行协调

        Args:
            fencing_token: Fencing Token
        """
        logger.debug(f"开始协调检查 (token={fencing_token})")

        # 1. 检测超时任务
        await self._check_timeout_tasks(fencing_token)

        # 2. 检测失联 Worker —— P3 已迁移到 LeaseSweeperLoop
        #    （Worker 失租 → Master sweep → 自动剔除 + 任务回收），
        #    这里不再做 last_heartbeat 阈值判活。

        # 3. 检测状态不一致
        await self._check_inconsistent_states(fencing_token)

        # 4. 清理僵尸任务
        await self._cleanup_zombie_tasks(fencing_token)

    async def _check_timeout_tasks(self, fencing_token: int):
        """检测超时任务（bulk_update 单条 UPDATE 即可）。

        Args:
            fencing_token: Fencing Token
        """
        try:
            # 查找运行中但超时的任务
            now = datetime.now(UTC)
            timeout_threshold = now - timedelta(seconds=self.timeout_threshold)

            timeout_ids = await TaskRun.filter(
                status=TaskStatus.RUNNING,
                start_time__lt=timeout_threshold,
            ).values_list("id", flat=True)

            if not timeout_ids:
                return

            logger.warning(f"发现 {len(timeout_ids)} 个超时任务")
            updated = await TaskRun.filter(id__in=list(timeout_ids)).update(
                status=TaskStatus.TIMEOUT,
                end_time=now,
                error_message=f"任务执行超时（超过 {self.timeout_threshold}秒）",
            )
            logger.info(f"已标记 {updated} 个任务为 TIMEOUT")

        except Exception:
            logger.exception("检测超时任务失败")

    async def _check_inconsistent_states(self, fencing_token: int):
        """检测状态不一致（拆 2 次 bulk update：有 error 走 FAILED，否则 SUCCESS）。

        Args:
            fencing_token: Fencing Token
        """
        try:
            # error_message 为空 → 推断为 SUCCESS
            success_ids = await TaskRun.filter(
                status=TaskStatus.RUNNING,
                end_time__isnull=False,
                error_message__isnull=True,
            ).values_list("id", flat=True)
            success_ids = list(success_ids)

            # error_message 非空 → FAILED
            failed_ids = await TaskRun.filter(
                status=TaskStatus.RUNNING,
                end_time__isnull=False,
                error_message__not_isnull=True,
            ).values_list("id", flat=True)
            failed_ids = list(failed_ids)

            total = len(success_ids) + len(failed_ids)
            if total == 0:
                return

            logger.warning(f"发现 {total} 个状态不一致任务")
            if success_ids:
                await TaskRun.filter(id__in=success_ids).update(status=TaskStatus.SUCCESS)
            if failed_ids:
                await TaskRun.filter(id__in=failed_ids).update(status=TaskStatus.FAILED)
            logger.info(f"修复 SUCCESS={len(success_ids)} FAILED={len(failed_ids)}")

        except Exception:
            logger.exception("检测状态不一致失败")

    async def _cleanup_zombie_tasks(self, fencing_token: int):
        """清理僵尸任务（bulk_update）。

        Args:
            fencing_token: Fencing Token
        """
        try:
            # 查找长时间处于 PENDING 状态的任务
            now = datetime.now(UTC)
            zombie_threshold = now - timedelta(hours=24)

            zombie_ids = await TaskRun.filter(
                status=TaskStatus.PENDING,
                created_at__lt=zombie_threshold,
            ).values_list("id", flat=True)
            zombie_ids = list(zombie_ids)

            if not zombie_ids:
                return

            logger.warning(f"发现 {len(zombie_ids)} 个僵尸任务")
            updated = await TaskRun.filter(id__in=zombie_ids).update(
                status=TaskStatus.FAILED,
                error_message="任务长时间未调度，已清理",
                end_time=now,
            )
            logger.info(f"已清理 {updated} 个僵尸任务")

        except Exception:
            logger.exception("清理僵尸任务失败")


# 全局协调循环实例
reconcile_loop = ReconcileLoop()
