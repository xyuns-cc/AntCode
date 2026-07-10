"""任务重试与补偿服务(Master 侧副本)

**P1-18 修复**:原实现把 pending retries 存到进程内 in-memory 队列,
Master 重启 / Leader 切换会永久丢失重试(retry 实际是 at-most-once)。

现在改用 Redis ZSet ``{ns}:retry:pending`` 做持久化 scheduled queue,
配合 ``{ns}:retry:processing`` hash 做崩溃恢复。原子 claim 用 Lua 一步
完成 ZRANGEBYSCORE+ZREM+HSET(见 ``_RETRY_CLAIM_LUA``);处理成功后
``HDEL`` processing;崩溃时下轮 tick 由 ``sweep_stalled`` 恢复。

对外 API (``schedule_retry`` / ``manual_retry`` / ``get_pending_retries`` /
``get_retry_stats`` / ``register_compensation_handler`` / ``start`` /
``stop``) 保持不变,scheduler_loop._schedule_retry 通过 schedule_retry
接口写入即可(不再摸 ``_retry_queue``)。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from antcode_core.common.config import settings
from antcode_core.domain.models.enums import TaskStatus
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from loguru import logger


class RetryStrategy(str, Enum):
    """重试策略"""

    FIXED = "fixed"
    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    CUSTOM = "custom"


class CompensationType(str, Enum):
    """补偿类型"""

    ROLLBACK = "rollback"
    CLEANUP = "cleanup"
    NOTIFY = "notify"
    RETRY_LATER = "retry_later"
    SKIP = "skip"


class RetryConfig:
    """重试配置"""

    def __init__(
        self,
        max_retries=3,
        strategy=RetryStrategy.EXPONENTIAL,
        base_delay=60,
        max_delay=3600,
        multiplier=2.0,
        jitter=True,
        retryable_errors=None,
        non_retryable_errors=None,
    ):
        self.max_retries = max_retries
        self.strategy = strategy
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.multiplier = multiplier
        self.jitter = jitter
        self.retryable_errors = retryable_errors or []
        self.non_retryable_errors = non_retryable_errors or [
            "AuthenticationError",
            "PermissionDenied",
            "InvalidConfiguration",
        ]


# ---------------------------------------------------------------------------
# Lua: 原子 claim —— ZRANGEBYSCORE + ZREM + HSET processing 一步完成
# ---------------------------------------------------------------------------
# KEYS[1] = pending ZSet, KEYS[2] = processing Hash
# ARGV[1] = now_ms, ARGV[2] = limit, ARGV[3] = claim_ms(processing value)
_RETRY_CLAIM_LUA = """
local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, tonumber(ARGV[2]))
if #due == 0 then return {} end
redis.call('ZREM', KEYS[1], unpack(due))
for i, payload in ipairs(due) do
    redis.call('HSET', KEYS[2], payload, ARGV[3])
end
return due
"""


class _RetryQueueBackend:
    """Redis ZSet 后端 —— schedule / claim / ack / sweep_stalled"""

    DEFAULT_PROCESSING_TIMEOUT_SEC = 60

    def __init__(self) -> None:
        self._namespace = settings.REDIS_NAMESPACE or "antcode"
        self._processing_timeout_sec = int(
            getattr(
                settings,
                "RETRY_PROCESSING_TIMEOUT_SEC",
                self.DEFAULT_PROCESSING_TIMEOUT_SEC,
            )
        )
        self._claim_sha: str | None = None
        self._script_lock = asyncio.Lock()

    def pending_key(self) -> str:
        return f"{self._namespace}:retry:pending"

    def processing_key(self) -> str:
        return f"{self._namespace}:retry:processing"

    @staticmethod
    def _encode(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _decode(raw: bytes | str) -> dict[str, Any] | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(f"retry payload 反序列化失败,丢弃: {exc}")
            return None

    async def _get_redis(self):
        from antcode_core.infrastructure.redis.client import get_redis_client

        return await get_redis_client()

    async def _ensure_script(self, redis) -> None:
        if self._claim_sha is not None:
            return
        async with self._script_lock:
            if self._claim_sha is None:
                self._claim_sha = await redis.script_load(_RETRY_CLAIM_LUA)

    async def schedule(
        self,
        *,
        task_id: Any,
        run_id: str,
        retry_time: datetime,
        retry_count: int,
    ) -> None:
        redis = await self._get_redis()
        payload = self._encode(
            {
                "task_id": task_id,
                "run_id": run_id,
                "retry_time": retry_time.isoformat(),
                "retry_count": retry_count,
            }
        )
        score = int(retry_time.timestamp() * 1000)
        await redis.zadd(self.pending_key(), {payload: score})

    async def claim_due(self, limit: int = 32) -> list[dict[str, Any]]:
        redis = await self._get_redis()
        await self._ensure_script(redis)
        now_ms = int(time.time() * 1000)
        try:
            raw_list = await redis.evalsha(
                self._claim_sha,
                2,
                self.pending_key(),
                self.processing_key(),
                str(now_ms),
                str(limit),
                str(now_ms),
            )
        except Exception as exc:
            if "NOSCRIPT" in str(exc):
                logger.warning("retry claim 脚本未在 Redis 缓存中,回退 EVAL")
                self._claim_sha = None
                raw_list = await redis.eval(
                    _RETRY_CLAIM_LUA,
                    2,
                    self.pending_key(),
                    self.processing_key(),
                    str(now_ms),
                    str(limit),
                    str(now_ms),
                )
            else:
                raise
        out: list[dict[str, Any]] = []
        for raw in raw_list or []:
            item = self._decode(raw)
            if item is not None:
                item["__raw_payload"] = (
                    raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
                )
                out.append(item)
        return out

    async def ack(self, raw_payload: str) -> None:
        redis = await self._get_redis()
        try:
            await redis.hdel(self.processing_key(), raw_payload)
        except Exception as exc:
            logger.warning(f"retry ack 失败(可忽略): {exc}")

    async def requeue(self, raw_payload: str, *, delay_seconds: int = 0) -> None:
        redis = await self._get_redis()
        score = int((time.time() + max(0, delay_seconds)) * 1000)
        try:
            pipe = redis.pipeline(transaction=True)
            pipe.zadd(self.pending_key(), {raw_payload: score})
            pipe.hdel(self.processing_key(), raw_payload)
            await pipe.execute()
        except Exception as exc:
            logger.warning(f"retry requeue 失败: {exc}")

    async def sweep_stalled(self) -> int:
        """崩溃恢复:把 processing hash 里超时的条目 requeue 回 ZSet。"""
        redis = await self._get_redis()
        now_ms = int(time.time() * 1000)
        threshold_ms = now_ms - self._processing_timeout_sec * 1000
        try:
            entries = await redis.hgetall(self.processing_key())
        except Exception as exc:
            logger.warning(f"retry sweep hgetall 失败: {exc}")
            return 0
        if not entries:
            return 0
        requeued = 0
        for raw, claim_ms_raw in entries.items():
            try:
                claim_ms = int(
                    claim_ms_raw.decode() if isinstance(claim_ms_raw, bytes) else claim_ms_raw
                )
            except (ValueError, AttributeError):
                claim_ms = 0
            if claim_ms > threshold_ms:
                continue
            raw_str = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
            await self.requeue(raw_str, delay_seconds=0)
            requeued += 1
        if requeued:
            logger.warning(
                f"retry sweep: 从 processing hash 恢复 {requeued} 条崩溃遗留任务"
            )
        return requeued

    async def peek_all(self) -> list[dict[str, Any]]:
        redis = await self._get_redis()
        raw_members = await redis.zrange(
            self.pending_key(), 0, -1, withscores=True
        )
        out: list[dict[str, Any]] = []
        for raw, score in raw_members or []:
            item = self._decode(raw)
            if item is None:
                continue
            item["_score_ms"] = int(score)
            out.append(item)
        return out


class _RetryQueueShim:
    """兼容旧调用点 ``retry_service._retry_queue.put({...})``。

    scheduler_loop._schedule_retry 直接 poke 私有属性 put 一个 dict
    (keys: task_id, run_id, retry_time, retry_count),这里做一个 shim
    把它翻译成 backend.schedule() 落 Redis ZSet。禁止改 scheduler_loop
    的前提下,只能这样兼容。
    """

    def __init__(self, backend: "_RetryQueueBackend") -> None:
        self._backend = backend

    async def put(self, item: dict[str, Any]) -> None:
        retry_time = item.get("retry_time")
        # 保底: 允许 None / naive datetime,直接落 now
        if not isinstance(retry_time, datetime):
            retry_time = datetime.now(UTC)
        elif retry_time.tzinfo is None:
            retry_time = retry_time.replace(tzinfo=UTC)
        await self._backend.schedule(
            task_id=item.get("task_id"),
            run_id=item.get("run_id") or "",
            retry_time=retry_time,
            retry_count=int(item.get("retry_count") or 0),
        )


class RetryService:
    """任务重试服务"""

    def __init__(self):
        self.default_config = RetryConfig()
        self.compensation_handlers = {}
        self._backend = _RetryQueueBackend()
        # 兼容 scheduler_loop._schedule_retry 里对 _retry_queue.put() 的旧调用
        self._retry_queue = _RetryQueueShim(self._backend)
        self._running = False
        self._worker_task: asyncio.Task | None = None

    async def start(self):
        """启动重试服务"""
        self._running = True
        self._worker_task = asyncio.create_task(self._run_loop())
        logger.info("任务重试服务已启动 (Redis ZSet 持久化)")

    async def stop(self):
        """停止重试服务"""
        self._running = False
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):
                pass
            self._worker_task = None
        logger.info("任务重试服务已停止")

    def calculate_delay(self, retry_count, config=None):
        """计算重试延迟时间"""
        config = config or self.default_config

        if config.strategy == RetryStrategy.FIXED:
            delay = config.base_delay
        elif config.strategy == RetryStrategy.EXPONENTIAL:
            delay = config.base_delay * (config.multiplier**retry_count)
        elif config.strategy == RetryStrategy.LINEAR:
            delay = config.base_delay * (retry_count + 1)
        else:
            delay = config.base_delay

        delay = min(delay, config.max_delay)

        if config.jitter:
            import random

            jitter_range = delay * 0.1
            delay = delay + random.uniform(-jitter_range, jitter_range)

        return int(delay)

    def should_retry(self, error, retry_count, config=None):
        """判断是否应该重试"""
        config = config or self.default_config

        if retry_count >= config.max_retries:
            return False

        for non_retryable in config.non_retryable_errors:
            if non_retryable.lower() in error.lower():
                return False

        if config.retryable_errors:
            return any(retryable.lower() in error.lower() for retryable in config.retryable_errors)

        return True

    async def schedule_retry(self, task, execution, error, config=None):
        """调度任务重试"""
        config = config or self._get_task_retry_config(task)
        current_retry = execution.retry_count

        if not self.should_retry(error, current_retry, config):
            logger.info(f"任务 {task.name} 不满足重试条件，执行补偿操作")
            await self._execute_compensation(task, execution, error)
            return None

        delay = self.calculate_delay(current_retry, config)
        next_retry_time = datetime.now(UTC) + timedelta(seconds=delay)

        execution.retry_count = current_retry + 1
        execution.status = TaskStatus.PENDING
        execution.error_message = f"重试 {execution.retry_count}/{config.max_retries}: {error}"
        await execution.save()

        task.failure_count += 1
        await task.save()

        await self._backend.schedule(
            task_id=task.id,
            run_id=execution.run_id,
            retry_time=next_retry_time,
            retry_count=execution.retry_count,
        )

        logger.info(
            f"任务 {task.name} 已调度重试 "
            f"({execution.retry_count}/{config.max_retries})，"
            f"延迟 {delay} 秒，下次执行时间: {next_retry_time}"
        )

        return next_retry_time

    async def manual_retry(self, run_id, user_id):
        """手动重试任务"""
        execution = await TaskRun.get_or_none(run_id=run_id)
        if not execution:
            return {"success": False, "error": "执行记录不存在"}

        task = await Task.get_or_none(id=execution.task_id)
        if not task:
            return {"success": False, "error": "任务不存在"}

        if execution.status == TaskStatus.RUNNING:
            return {"success": False, "error": "任务正在执行中"}

        execution.status = TaskStatus.PENDING
        execution.retry_count += 1
        execution.error_message = f"手动重试 by user {user_id}"
        await execution.save()

        from antcode_master.control.scheduler_loop import scheduler_service

        await scheduler_service.trigger_task(task.id)

        logger.info(f"任务 {task.name} 已手动触发重试 by user {user_id}")

        return {
            "success": True,
            "message": "任务已触发重试",
            "run_id": run_id,
            "retry_count": execution.retry_count,
        }

    def register_compensation_handler(self, task_type, handler):
        """注册补偿处理器"""
        self.compensation_handlers[task_type] = handler
        logger.info(f"已注册补偿处理器: {task_type}")

    async def _execute_compensation(self, task, execution, error):
        """执行补偿操作"""
        try:
            task.status = TaskStatus.FAILED
            task.failure_count += 1
            await task.save()

            execution.status = TaskStatus.FAILED
            execution.end_time = datetime.now(UTC)
            execution.error_message = f"重试耗尽: {error}"
            await execution.save()

            task_type = str(task.task_type.value) if task.task_type else "default"
            handler = self.compensation_handlers.get(task_type)

            if handler:
                await handler(task, execution, error)
                logger.info(f"任务 {task.name} 补偿处理完成")

            await self._send_failure_alert(task, execution, error)

        except Exception as e:
            logger.error(f"执行补偿操作失败: {e}")

    async def _send_failure_alert(self, task, execution, error):
        """发送任务失败告警"""
        try:
            from antcode_core.application.services.alert import alert_service

            alert_message = (
                f"任务执行失败告警\n"
                f"任务名称: {task.name}\n"
                f"执行ID: {execution.run_id}\n"
                f"重试次数: {execution.retry_count}\n"
                f"错误信息: {error}\n"
                f"失败时间: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}"
            )

            await alert_service.send_alert(
                title=f"任务失败: {task.name}", content=alert_message, level="error"
            )

        except Exception as e:
            logger.error(f"发送任务失败告警失败: {e}")

    async def _run_loop(self):
        """从 Redis ZSet 原子 claim 到期任务并 trigger。

        #15: 入口加 leader 闸口 —— 双 Master 场景下,非 Leader 不能 trigger。
        P1-18: 每轮 sweep_stalled 恢复崩溃遗留 -> claim_due -> trigger -> ack。
        Master 重启 / 切主后 ZSet 数据仍在 Redis,新 Leader 直接接管。
        """
        from antcode_master.leader import ensure_leader

        sweep_every_n = 10
        tick_counter = 0

        while self._running:
            try:
                if not await ensure_leader():
                    # 非 Leader,空转;不消费队列 —— 让 Leader 处理
                    await asyncio.sleep(1.0)
                    continue

                tick_counter += 1
                if tick_counter % sweep_every_n == 1:
                    try:
                        await self._backend.sweep_stalled()
                    except Exception as exc:
                        logger.warning(f"retry sweep_stalled 异常: {exc}")

                try:
                    claimed = await self._backend.claim_due(limit=32)
                except Exception as exc:
                    logger.error(f"retry claim_due 异常: {exc}")
                    await asyncio.sleep(1)
                    continue

                if not claimed:
                    await asyncio.sleep(1.0)
                    continue

                for item in claimed:
                    raw_payload = item.get("__raw_payload", "")
                    task_id = item.get("task_id")
                    try:
                        from antcode_master.control.scheduler_loop import (
                            scheduler_service,
                        )

                        await scheduler_service.trigger_task(task_id)
                        logger.info(f"任务 {task_id} 重试已触发")
                        await self._backend.ack(raw_payload)
                    except Exception as exc:
                        logger.error(
                            f"trigger_task({task_id}) 失败,requeue: {exc}"
                        )
                        await self._backend.requeue(raw_payload, delay_seconds=5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"处理重试队列失败: {e}")
                await asyncio.sleep(1)

    def _get_task_retry_config(self, task):
        """获取任务的重试配置"""
        return RetryConfig(
            max_retries=task.retry_count,
            base_delay=task.retry_delay,
            strategy=RetryStrategy.EXPONENTIAL,
        )

    async def get_retry_stats(self, task_id):
        """获取任务重试统计"""
        executions = await TaskRun.filter(task_id=task_id).all()

        total_executions = len(executions)
        retried_executions = sum(1 for e in executions if e.retry_count > 0)
        total_retries = sum(e.retry_count for e in executions)

        retry_success = sum(
            1 for e in executions if e.retry_count > 0 and e.status == TaskStatus.SUCCESS
        )
        retry_success_rate = (
            retry_success / retried_executions * 100 if retried_executions > 0 else 0
        )

        return {
            "task_id": task_id,
            "total_executions": total_executions,
            "retried_executions": retried_executions,
            "total_retries": total_retries,
            "retry_success_count": retry_success,
            "retry_success_rate": round(retry_success_rate, 2),
            "avg_retries_per_execution": (
                round(total_retries / retried_executions, 2) if retried_executions > 0 else 0
            ),
        }

    async def get_pending_retries(self):
        """获取待重试的任务列表(Redis ZSet 只读快照)"""
        try:
            snapshot = await self._backend.peek_all()
        except Exception as exc:
            logger.warning(f"peek pending retries 失败: {exc}")
            return []

        pending = []
        for item in snapshot:
            pending.append(
                {
                    "task_id": item.get("task_id"),
                    "run_id": item.get("run_id"),
                    "retry_time": item.get("retry_time"),
                    "retry_count": item.get("retry_count"),
                }
            )
        return pending


retry_service = RetryService()
