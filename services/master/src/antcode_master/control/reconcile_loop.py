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
from datetime import UTC, datetime

from antcode_core.domain.models import TaskRun
from antcode_core.domain.models.enums import TaskStatus
from loguru import logger

from antcode_master.control.reconcile_repairs import (
    cleanup_zombie_tasks,
    repair_inconsistent_states,
)
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
            f"协调循环已启动: check_interval={self.check_interval}s, timeout_threshold={self.timeout_threshold}s"
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

        # 4b. 复审 F3-A/F2-A: 创建后未派发的孤儿 run + 卡 busy 的 Task 状态。
        from antcode_master.control.reconcile_repairs import (
            repair_stale_task_status,
            repair_stuck_queued_runs,
        )

        await repair_stuck_queued_runs()
        await repair_stale_task_status()

        # 5. P2 §4.3: global control stream 安全裁剪（详见模块注释）。
        from antcode_master.control.global_stream_retention import trim_global_control_stream

        await trim_global_control_stream()

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
            candidates = (
                await TaskRun.filter(status=TaskStatus.RUNNING)
                .only(
                    "id",
                    "run_id",
                    "start_time",
                    "last_heartbeat",
                    "created_at",
                    "task_id",
                )
                .all()
            )

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
                    error_message="任务执行超时（超过 per-run timeout）",
                )
                if ok:
                    marked += 1
            logger.info(f"已标记 {marked}/{len(to_mark)} 个任务为 TIMEOUT")

        except Exception:
            logger.exception("检测超时任务失败")

    async def _check_dispatched_no_ack(self, fencing_token: int):
        """P1-17 / P1-FN-06: 委托 dispatch_ack_liveness(见该模块注释)。

        僵尸分发按 ``DISPATCH_ACK_TIMEOUT_SECONDS`` 收敛为 FAILED;绑定
        Worker 的 Lease 仍存活的 run 跳过并延长观察,避免 result loop 积压
        时误杀仍在执行的长任务。
        """
        from antcode_master.control.dispatch_ack_liveness import check_dispatched_no_ack

        await check_dispatched_no_ack(self.DISPATCH_ACK_TIMEOUT_SECONDS)

    async def _check_inconsistent_states(self, fencing_token: int):
        """检测状态不一致并通过统一状态机收敛终态。

        Args:
            fencing_token: Fencing Token
        """
        await repair_inconsistent_states()

    async def _cleanup_zombie_tasks(self, fencing_token: int):
        """清理僵尸任务（bulk_update）。

        Args:
            fencing_token: Fencing Token
        """
        await cleanup_zombie_tasks()


# 全局协调循环实例
reconcile_loop = ReconcileLoop()
