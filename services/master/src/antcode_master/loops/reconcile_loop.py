"""
协调循环 (Reconcile Loop)

负责处理：
- 超时任务检测与恢复
- 失联 Worker 检测
- 状态不一致补偿
- 僵尸任务清理
"""

import asyncio
import contextlib
from datetime import datetime, timedelta

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
            except Exception as e:
                logger.error(f"协调循环异常: {e}")
                await asyncio.sleep(self.check_interval)

    async def _reconcile(self, fencing_token: int):
        """执行协调

        Args:
            fencing_token: Fencing Token
        """
        logger.debug(f"开始协调检查 (token={fencing_token})")

        # 1. 检测超时任务
        await self._check_timeout_tasks(fencing_token)

        # 2. 检测失联 Worker
        await self._check_disconnected_workers(fencing_token)

        # 3. 检测状态不一致
        await self._check_inconsistent_states(fencing_token)

        # 4. 清理僵尸任务
        await self._cleanup_zombie_tasks(fencing_token)

    async def _check_timeout_tasks(self, fencing_token: int):
        """检测超时任务

        Args:
            fencing_token: Fencing Token
        """
        try:
            from antcode_core.domain.models import TaskRun
            from antcode_core.domain.models.enums import TaskStatus

            # 查找运行中但超时的任务
            timeout_threshold = datetime.now() - timedelta(seconds=self.timeout_threshold)

            timeout_tasks = await TaskRun.filter(
                status=TaskStatus.RUNNING,
                start_time__lt=timeout_threshold,
            ).all()

            if timeout_tasks:
                logger.warning(f"发现 {len(timeout_tasks)} 个超时任务")

                for task in timeout_tasks:
                    logger.info(f"标记任务超时: run_id={task.id}")
                    task.status = TaskStatus.TIMEOUT
                    task.end_time = datetime.now()
                    task.error_message = f"任务执行超时（超过 {self.timeout_threshold}秒）"
                    await task.save()

        except Exception as e:
            logger.error(f"检测超时任务失败: {e}")

    async def _check_disconnected_workers(self, fencing_token: int):
        """检测失联 Worker

        Args:
            fencing_token: Fencing Token
        """
        try:
            from antcode_core.domain.models import Worker
            from antcode_core.domain.models.enums import WorkerStatus

            # 查找失联的 Worker
            offline_threshold = datetime.now() - timedelta(seconds=60)

            disconnected_workers = await Worker.filter(
                status=WorkerStatus.ONLINE.value,
                last_heartbeat__lt=offline_threshold,
            ).all()

            if disconnected_workers:
                logger.warning(f"发现 {len(disconnected_workers)} 个失联 Worker")

                for worker in disconnected_workers:
                    logger.info(f"标记 Worker 离线: worker_id={worker.id}")
                    worker.status = WorkerStatus.OFFLINE.value
                    await worker.save()

                    # 处理该 Worker 上的运行中任务
                    await self._handle_worker_tasks(worker.id, fencing_token)

        except Exception as e:
            logger.error(f"检测失联 Worker 失败: {e}")

    async def _handle_worker_tasks(self, worker_id: int, fencing_token: int):
        """处理 Worker 上的任务

        Args:
            worker_id: Worker ID
            fencing_token: Fencing Token
        """
        try:
            from antcode_core.domain.models import TaskRun
            from antcode_core.domain.models.enums import TaskStatus

            # 查找该 Worker 上运行中的任务
            running_tasks = await TaskRun.filter(
                worker_id=worker_id,
                status=TaskStatus.RUNNING,
            ).all()

            if running_tasks:
                logger.info(f"Worker {worker_id} 上有 {len(running_tasks)} 个运行中任务")

                for task in running_tasks:
                    logger.info(f"标记任务失败: run_id={task.id}")
                    task.status = TaskStatus.FAILED
                    task.end_time = datetime.now()
                    task.error_message = "Worker 失联"
                    await task.save()

        except Exception as e:
            logger.error(f"处理 Worker 任务失败: {e}")

    async def _check_inconsistent_states(self, fencing_token: int):
        """检测状态不一致

        Args:
            fencing_token: Fencing Token
        """
        try:
            from antcode_core.domain.models import TaskRun
            from antcode_core.domain.models.enums import TaskStatus

            # 查找状态不一致的任务（例如：有 end_time 但状态仍为 RUNNING）
            inconsistent_tasks = await TaskRun.filter(
                status=TaskStatus.RUNNING,
                end_time__isnull=False,
            ).all()

            if inconsistent_tasks:
                logger.warning(f"发现 {len(inconsistent_tasks)} 个状态不一致任务")

                for task in inconsistent_tasks:
                    logger.info(f"修复任务状态: run_id={task.id}")
                    # 根据 end_time 和其他信息推断正确状态
                    if task.error_message:
                        task.status = TaskStatus.FAILED
                    else:
                        task.status = TaskStatus.SUCCESS
                    await task.save()

        except Exception as e:
            logger.error(f"检测状态不一致失败: {e}")

    async def _cleanup_zombie_tasks(self, fencing_token: int):
        """清理僵尸任务

        Args:
            fencing_token: Fencing Token
        """
        try:
            from antcode_core.domain.models import TaskRun
            from antcode_core.domain.models.enums import TaskStatus

            # 查找长时间处于 PENDING 状态的任务
            zombie_threshold = datetime.now() - timedelta(hours=24)

            zombie_tasks = await TaskRun.filter(
                status=TaskStatus.PENDING,
                created_at__lt=zombie_threshold,
            ).all()

            if zombie_tasks:
                logger.warning(f"发现 {len(zombie_tasks)} 个僵尸任务")

                for task in zombie_tasks:
                    logger.info(f"清理僵尸任务: run_id={task.id}")
                    task.status = TaskStatus.FAILED
                    task.error_message = "任务长时间未调度，已清理"
                    task.end_time = datetime.now()
                    await task.save()

        except Exception as e:
            logger.error(f"清理僵尸任务失败: {e}")


# 全局协调循环实例
reconcile_loop = ReconcileLoop()
