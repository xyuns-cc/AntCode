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
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, TaskStatus
from loguru import logger
from tortoise.expressions import Q

from antcode_master.leader import ensure_leader, get_fencing_token


class ReconcileLoop:
    """协调循环"""

    # P1-17: 分布式任务分发后节点还没上报 RUNNING 的最大容忍时长（秒）。
    # 覆盖场景:worker 收到 XREADGROUP 的任务后、进入 _execute_task 前崩溃
    # (进程被 OOM kill / 断电 / kubelet 重启) —— 此时 dispatch_status 已经写
    # 到 DISPATCHED/DISPATCHING,但 runtime_status 永远是 NULL/PENDING/QUEUED,
    # 旧实现只查 RUNNING 完全捞不到,任务永卡 DISPATCHING。
    #
    # 默认 180s = 3 分钟:大于常见冷启动(bundle 下载 + venv 准备)时长,
    # 又能在业务感知得到的时窗内失败并进入补派/重试链路。
    DISPATCH_ACK_TIMEOUT_SECONDS = 180

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

        # 1b. P1-17: 检测"分发出去但节点没上报 RUNNING"的僵尸分发
        #     (worker 收到任务后崩溃在 runtime 准备阶段的场景)
        await self._check_dispatched_no_ack(fencing_token)

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
            # R1-P1-2 (审查报告): 原实现 (a) 用全局 300s 阈值一刀切，正常
            # 派发默认 timeout 3600 的爬虫会被误杀；(b) bulk update 绕过
            # 状态机，只写 status 不写 runtime_status，同 P1-1 机制迟到
            # SUCCESS 可翻回。
            # 修复策略：只对确实超过 per-run timeout 的记录标 TIMEOUT，
            # 且走 execution_status_service.update_runtime_status 让终态
            # 保护生效。R1-P2-23 同时兜住 start_time 为 NULL 的记录
            # （改用 last_heartbeat / created_at 兜底）。
            from antcode_core.application.services.scheduler.execution_status_service import (
                execution_status_service,
            )
            from antcode_core.domain.models.enums import RuntimeStatus

            now = datetime.now(UTC)
            # 拉活跃 RUNNING 记录：per-run 判定
            candidates = await TaskRun.filter(status=TaskStatus.RUNNING).only(
                "id", "run_id", "start_time", "last_heartbeat", "created_at",
                "task_id",
            ).all()

            if not candidates:
                return

            # 尝试拿到 per-task timeout；缺失时回退到全局阈值
            timeout_map: dict[int, int] = {}
            task_ids = [c.task_id for c in candidates if c.task_id]
            if task_ids:
                from antcode_core.domain.models import Task
                for t in await Task.filter(id__in=task_ids).only("id", "timeout_seconds").all():
                    timeout_map[t.id] = int(getattr(t, "timeout_seconds", 0) or 0)

            to_mark: list[TaskRun] = []
            fallback_threshold = int(self.timeout_threshold or 300)
            for run in candidates:
                # 优先按 start_time；R1-P2-23: NULL 时用 last_heartbeat 或 created_at 兜底
                anchor = run.start_time or run.last_heartbeat or run.created_at
                if not anchor:
                    continue
                per_run_to = timeout_map.get(run.task_id, 0) or fallback_threshold
                elapsed = (now - anchor).total_seconds()
                if elapsed > per_run_to:
                    to_mark.append(run)

            if not to_mark:
                return
            logger.warning(f"发现 {len(to_mark)} 个超时任务，走状态机 CAS")
            marked = 0
            for run in to_mark:
                ok = await execution_status_service.update_runtime_status(
                    run_id=run.run_id,
                    status=RuntimeStatus.TIMEOUT,
                    status_at=now,
                    error_message=f"任务执行超时（超过 per-run timeout）",
                )
                if ok:
                    marked += 1
            logger.info(f"已标记 {marked}/{len(to_mark)} 个任务为 TIMEOUT")

        except Exception:
            logger.exception("检测超时任务失败")

    async def _check_dispatched_no_ack(self, fencing_token: int):
        """P1-17: 检测"已分发到 Worker 但 Worker 从未上报 RUNNING"的僵尸分发。

        触发场景:``scheduler_loop._record_dispatch_result`` 把 dispatch_status
        置为 DISPATCHED,``worker.Engine._execute_task`` 内部会主动上报一次
        status="running" 把 runtime_status 推到 RUNNING。如果 Worker 在
        "收到任务 → 上报 RUNNING" 之间的窗口崩溃(常见于 bundle 下载
        阶段被 OOM kill / 节点断电 / kubelet 重启),master 侧就出现:

        - dispatch_status = DISPATCHING 或 DISPATCHED
        - runtime_status  = NULL / QUEUED / PENDING
        - dispatch_updated_at 老于 ``DISPATCH_ACK_TIMEOUT_SECONDS``

        旧实现只查 ``TaskStatus.RUNNING`` 完全捞不到,任务永卡 DISPATCHING、
        不失败也不超时也不补派。这里通过 ``update_dispatch_status(FAILED)``
        统一收敛终态,由 status_service 的 CAS 保证:
        (a) 一旦 worker 迟到 RUNNING 上来就命中 dispatch 终态吸收,不会翻转;
        (b) 迟到的 SUCCESS 也不会复活 —— runtime_status 终态保护。
        """
        from antcode_core.application.services.scheduler.execution_status_service import (
            execution_status_service,
        )

        try:
            now = datetime.now(UTC)
            cutoff = now - timedelta(seconds=int(self.DISPATCH_ACK_TIMEOUT_SECONDS))

            # 命中条件:dispatch 侧已发出去(DISPATCHING/DISPATCHED),runtime 侧
            # 还没进入 RUNNING (NULL / QUEUED / PENDING 都算未 ACK),且超阈值。
            candidates = (
                await TaskRun.filter(
                    Q(dispatch_status__in=[
                        DispatchStatus.DISPATCHING,
                        DispatchStatus.DISPATCHED,
                    ]),
                    Q(runtime_status__isnull=True) | Q(runtime_status=RuntimeStatus.QUEUED),
                    Q(dispatch_updated_at__lt=cutoff)
                    | Q(dispatch_updated_at__isnull=True, created_at__lt=cutoff),
                )
                .only("id", "run_id", "dispatch_status", "runtime_status", "dispatch_updated_at")
                .limit(200)
                .all()
            )
            if not candidates:
                return

            logger.warning(
                f"P1-17: 发现 {len(candidates)} 个已分发但节点未上报 RUNNING 的僵尸任务"
            )
            marked = 0
            for run in candidates:
                # 走 dispatch_status → FAILED,复用 _derive_overall 把 status 同步为
                # TaskStatus.FAILED,并由 execution_status_service 触发 Task 计数。
                ok = await execution_status_service.update_dispatch_status(
                    run_id=run.run_id,
                    status=DispatchStatus.FAILED,
                    status_at=now,
                    error_message=(
                        f"节点未在 {self.DISPATCH_ACK_TIMEOUT_SECONDS}s 内上报 RUNNING"
                        "(worker 可能在收到任务后崩溃)"
                    ),
                )
                if ok:
                    marked += 1
            logger.info(
                f"P1-17: 已标记 {marked}/{len(candidates)} 条僵尸分发为 FAILED"
            )
        except Exception:
            logger.exception("P1-17: 检测僵尸分发失败")

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
