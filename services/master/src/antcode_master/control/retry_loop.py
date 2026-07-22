"""任务重试与补偿服务(Master 侧副本)

**P1-18 修复**:原实现把 pending retries 存到进程内 in-memory 队列,
Master 重启 / Leader 切换会永久丢失重试(retry 实际是 at-most-once)。

现在改用 Redis ZSet ``{ns}:retry:pending`` 做持久化 scheduled queue,
配合 ``{ns}:retry:processing`` hash 做崩溃恢复。原子 claim 用 Lua 一步
完成 ZRANGEBYSCORE+ZREM+HSET(见 ``_RETRY_CLAIM_LUA``);处理成功后
``HDEL`` processing;崩溃时下轮 tick 由 ``sweep_stalled`` 恢复。

``scheduler_loop._schedule_retry`` 先在 TaskRun 上原子创建 durable intent，
再通过 ``schedule_intent`` 投递 Redis；retry loop 真正创建新 TaskRun 后才
清理数据库 intent 并 ACK processing entry。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from antcode_core.common.config import settings
from antcode_core.domain.models.enums import TaskStatus
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from loguru import logger


class RetryStrategy(StrEnum):
    """重试策略"""

    FIXED = "fixed"
    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    CUSTOM = "custom"


class CompensationType(StrEnum):
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


# G3: 单条 claimed intent 的最大处理尝试次数(含首次)。持续失败的 payload
# 超过该次数后不再 requeue —— 记录完整 payload 并丢弃,避免 poison intent
# 以 ~5s 周期 claim→raise→requeue 无限循环。
MAX_CLAIM_ATTEMPTS = 5


# P1-FN-10: task 合法 busy 时抛此异常，requeue 但不计入 MAX_CLAIM_ATTEMPTS，避免 busy → 被误判 poison 丢弃。
class RetryClaimBusyError(RuntimeError):
    """Retry intent 无法立即消费(target task busy),需 requeue 但不算失败。"""

    pass


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

    def attempts_key(self) -> str:
        return f"{self._namespace}:retry:attempts"

    @staticmethod
    def _encode(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _decode(raw: bytes | str) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("retry payload 必须是 JSON object")
        return payload

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
            # V4: 单条 payload 解码失败(损坏/注入的非 dict / 非法 JSON)不能
            # 让整批 claim_due 抛出 —— 否则本批已被 Lua 移入 processing 的其它
            # 好条目全部悬空未 ack,sweep_stalled 又会把坏条目重新喂回,永久
            # 楔死整条 retry 流水线。这里逐条 try/except:坏 payload 记日志后
            # 直接从 processing hash 移除(丢弃、不 requeue),继续处理好条目。
            try:
                item = self._decode(raw)
            except Exception as exc:
                logger.error(f"retry claim 解码失败,丢弃坏 payload: raw={raw!r} exc={exc}")
                try:
                    await redis.hdel(self.processing_key(), raw)
                except Exception as del_exc:
                    logger.warning(f"丢弃坏 retry payload 时清理 processing 失败: {del_exc}")
                continue
            item["__raw_payload"] = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            out.append(item)
        return out

    async def ack(self, raw_payload: str) -> None:
        redis = await self._get_redis()
        removed = await redis.hdel(self.processing_key(), raw_payload)
        if int(removed or 0) != 1:
            raise RuntimeError("retry ack did not remove the processing entry")

    async def incr_attempts(self, run_id: str) -> int:
        """V3: 以 run_id 为键持久化 claim 处理失败次数,独立于 payload 字节。

        ``_recover_from_db`` 用 ``schedule()`` 重新注入的 payload 不含 attempts,
        但计数活在这个 hash 里,不会被重置 —— MAX_CLAIM_ATTEMPTS 因此能正常
        触顶,poison intent 不再以 ~5s 周期无限 claim→raise→requeue。
        """
        redis = await self._get_redis()
        return int(await redis.hincrby(self.attempts_key(), run_id, 1))

    async def clear_attempts(self, run_id: str) -> None:
        """成功创建 retry run 或丢弃 poison intent 后清理该 run 的计数。"""
        if not run_id:
            return
        redis = await self._get_redis()
        await redis.hdel(self.attempts_key(), run_id)

    async def requeue(
        self,
        raw_payload: str,
        *,
        delay_seconds: int = 0,
        replacement: str | None = None,
    ) -> None:
        """把 processing 中的条目放回 pending。

        G3: ``replacement`` 非 None 时用新 payload 入队(用于附带 attempts
        计数),processing hash 仍按原 raw_payload 清理。
        """
        redis = await self._get_redis()
        score = int((time.time() + max(0, delay_seconds)) * 1000)
        pipe = redis.pipeline(transaction=True)
        pipe.zadd(self.pending_key(), {(replacement if replacement is not None else raw_payload): score})
        pipe.hdel(self.processing_key(), raw_payload)
        results = await pipe.execute()
        if len(results) < 2 or int(results[1] or 0) != 1:
            raise RuntimeError("retry requeue did not clear the processing entry")

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
                claim_ms = int(claim_ms_raw.decode() if isinstance(claim_ms_raw, bytes) else claim_ms_raw)
            except (ValueError, AttributeError):
                claim_ms = 0
            if claim_ms > threshold_ms:
                continue
            raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            await self.requeue(raw_str, delay_seconds=0)
            requeued += 1
        if requeued:
            logger.warning(f"retry sweep: 从 processing hash 恢复 {requeued} 条崩溃遗留任务")
        return requeued

    async def peek_all(self) -> list[dict[str, Any]]:
        redis = await self._get_redis()
        raw_members = await redis.zrange(self.pending_key(), 0, -1, withscores=True)
        out: list[dict[str, Any]] = []
        for raw, score in raw_members or []:
            item = self._decode(raw)
            if item is None:
                continue
            item["_score_ms"] = int(score)
            out.append(item)
        return out


class RetryService:
    """任务重试服务"""

    def __init__(self):
        self.default_config = RetryConfig()
        self.compensation_handlers = {}
        self._backend = _RetryQueueBackend()
        self._running = False
        self._worker_task: asyncio.Task | None = None

    async def schedule_intent(self, intent) -> None:
        """将已经持久化到 source TaskRun 的 intent 投递到 Redis。"""
        await self._backend.schedule(
            task_id=intent.task_id,
            run_id=intent.source_run_id,
            retry_time=intent.retry_time,
            retry_count=intent.retry_count,
        )

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
                alert_message,
                level="ERROR",
                source="scheduler",
                extra={"title": f"任务失败: {task.name}"},
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
                        await self._recover_from_db()
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
                    task_id = item.get("task_id")
                    new_run_id = await self._handle_claimed_item(item)
                    if new_run_id is not None:
                        logger.info(f"任务 {task_id} 重试已创建: run_id={new_run_id}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"处理重试队列失败: {e}")
                await asyncio.sleep(1)

    async def _handle_claimed_item(self, item: dict[str, Any]) -> str | None:
        """处理单条 claimed intent。

        G3: 原实现对任何异常都无条件 requeue(delay 5s),永久无效的 intent
        (例如 ``_validate_retry_source`` 判定 source 不存在/已失效)会以
        ~5s 周期无限循环。现在:

        * ``RetryIntentInvalidError``(永久无效)→ 立即记录完整 payload 并丢弃;
        * 其它异常 → 按 run_id 累计 attempts,超过 ``MAX_CLAIM_ATTEMPTS`` 后
          记录完整 payload 并丢弃。

        V3: attempts 计数以 run_id 为键持久化在 Redis hash(见
        ``_RetryQueueBackend.incr_attempts``),独立于 payload 字节 ——
        ``_recover_from_db`` 用 ``schedule()`` 重新注入(不带 attempts)也无法
        把计数清零,避免 poison intent 复现 ~5s 循环并重复入队。达上限丢弃时
        额外清 DB ``next_retry_at``(``_abandon_durable_intent``),阻止恢复
        循环再注入。
        """
        from antcode_master.control.retry_intent_guard import RetryIntentInvalidError

        raw_payload = str(item.get("__raw_payload") or "")
        source_run_id = str(item.get("run_id") or "")
        try:
            return await self._process_claimed_intent(item)
        except RetryIntentInvalidError as exc:
            logger.error(f"retry intent 永久无效,直接丢弃: payload={raw_payload} exc={exc}")
            await self._discard_claimed(raw_payload, source_run_id)
            return None
        except RetryClaimBusyError as exc:
            # P1-FN-10: 合法 busy 不计 attempts，换 30s 长退避等 task 空闲。
            logger.info(
                f"retry intent target task busy,延后 30s requeue(不计入 poison): "
                f"task_id={item.get('task_id')} exc={exc}"
            )
            await self._backend.requeue(raw_payload, delay_seconds=30)
            return None
        except Exception as exc:
            attempts = await self._incr_claimed_attempts(source_run_id)
            if attempts >= MAX_CLAIM_ATTEMPTS:
                logger.error(f"创建 retry run 连续失败 {attempts} 次,放弃并丢弃: payload={raw_payload} exc={exc}")
                await self._discard_claimed(raw_payload, source_run_id)
                await self._abandon_durable_intent(source_run_id)
                return None
            logger.error(
                f"创建 retry run 失败({attempts}/{MAX_CLAIM_ATTEMPTS}),requeue: task_id={item.get('task_id')} exc={exc}"
            )
            await self._backend.requeue(raw_payload, delay_seconds=5)
            return None

    async def _incr_claimed_attempts(self, source_run_id: str) -> int:
        """按 run_id 自增 claim 失败计数;无 source run_id 的坏 payload 无法
        重试,直接判定到顶让上层丢弃。"""
        if not source_run_id:
            return MAX_CLAIM_ATTEMPTS
        return await self._backend.incr_attempts(source_run_id)

    async def _discard_claimed(self, raw_payload: str, source_run_id: str = "") -> None:
        """丢弃 poison payload:清 processing hash + attempts 计数,不再回 pending。"""
        try:
            await self._backend.ack(raw_payload)
        except Exception as exc:
            # ack 失败说明 processing entry 已被 sweep 等旁路清走,记录即可,
            # 不让丢弃动作打断本轮 claim 批次。
            logger.warning(f"丢弃 retry payload 时清理 processing 失败: {exc}")
        if source_run_id:
            try:
                await self._backend.clear_attempts(source_run_id)
            except Exception as exc:
                logger.warning(f"丢弃 retry payload 时清理 attempts 计数失败: run_id={source_run_id} exc={exc}")

    async def _abandon_durable_intent(self, source_run_id: str) -> None:
        """达 MAX_CLAIM_ATTEMPTS 丢弃 poison intent 时清 DB ``next_retry_at``,
        阻止 ``_recover_from_db`` 每轮 sweep 再把它注入 pending 形成 poison 循环。

        P1-FN-04: 等待重试的 run（PENDING）同时显式收敛为 FAILED —— 用户
        必须能从 run 状态看到自动重试已放弃，而不是 run 永远停在 PENDING。
        """
        if not source_run_id:
            return
        try:
            finalized = await TaskRun.filter(
                run_id=source_run_id,
                next_retry_at__not_isnull=True,
                status=TaskStatus.PENDING,
            ).update(
                next_retry_at=None,
                status=TaskStatus.FAILED,
                end_time=datetime.now(UTC),
                error_message=f"自动重试已放弃: 连续 {MAX_CLAIM_ATTEMPTS} 次创建重试 run 失败",
            )
            if not finalized:
                # run 状态已被其它路径推进（取消/新结果）：只清 intent 标记。
                await TaskRun.filter(
                    run_id=source_run_id,
                    next_retry_at__not_isnull=True,
                ).update(next_retry_at=None)
        except Exception as exc:
            logger.warning(f"清理 poison retry intent 的 DB 标记失败: run_id={source_run_id} exc={exc}")

    async def _process_claimed_intent(self, item: dict[str, Any]) -> str:
        """创建 retry run、清理 durable intent，最后 ACK Redis claim。"""
        from antcode_master.control.scheduler_loop import RetryIntent, scheduler_service

        raw_payload = str(item.get("__raw_payload") or "")
        source_run_id = str(item.get("run_id") or "")
        if not raw_payload or not source_run_id:
            raise RuntimeError("retry payload 缺少 raw payload 或 source run_id")
        intent = RetryIntent(
            task_id=int(item["task_id"]),
            source_run_id=source_run_id,
            retry_count=int(item["retry_count"]),
            retry_time=datetime.fromisoformat(str(item["retry_time"])),
        )
        new_run_id = await scheduler_service.trigger_retry_intent(intent)
        await self._clear_durable_intent(source_run_id)
        await self._backend.ack(raw_payload)
        return new_run_id

    async def _clear_durable_intent(self, source_run_id: str) -> None:
        # P1-FN-05: 权威清除已并入 Master 创建新 run 的同一事务
        # (scheduler_loop._consume_retry_intent);此处仅为提交后校验 + 兜底补清。
        cleared = await TaskRun.filter(run_id=source_run_id, next_retry_at__not_isnull=True).update(next_retry_at=None)
        if cleared != 1:
            source = await TaskRun.get_or_none(run_id=source_run_id)
            if source is not None and source.next_retry_at is not None:
                raise RuntimeError(f"durable retry intent 清理失败: run_id={source_run_id}")
        # V3: 成功创建 retry run 后重置该 run 的 attempts 计数(仅在成功/清理时
        # 归零,保证真正的瞬态错误仍能继续累计并最终触顶 MAX_CLAIM_ATTEMPTS)。
        await self._backend.clear_attempts(source_run_id)

    async def _recover_from_db(self) -> int:
        """Rebuild Redis retry entries from durable TaskRun intent."""
        pending = (
            await TaskRun.filter(
                next_retry_at__not_isnull=True,
            )
            .only("task_id", "run_id", "retry_count", "next_retry_at")
            .limit(500)
        )
        recovered = 0
        for execution in pending:
            retry_time = execution.next_retry_at
            if retry_time is None:
                continue
            await self._backend.schedule(
                task_id=execution.task_id,
                run_id=execution.run_id,
                retry_time=retry_time,
                retry_count=execution.retry_count,
            )
            recovered += 1
        if recovered:
            logger.info(f"从 TaskRun 恢复 retry 意图: {recovered} 条")
        return recovered

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

        retry_success = sum(1 for e in executions if e.retry_count > 0 and e.status == TaskStatus.SUCCESS)
        retry_success_rate = retry_success / retried_executions * 100 if retried_executions > 0 else 0

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
