"""任务重试与补偿服务

**P1-18 修复**:原实现把 pending retries 存到进程内 in-memory 队列,
Master 重启 / Leader 切换会永久丢失重试(retry 实际是 at-most-once)。

现在改用 Redis ZSet ``{ns}:retry:pending`` 做持久化 scheduled queue:

- ``_drain_due_retries`` 每秒 tick,用 **原子** Lua 脚本
  (``_RETRY_CLAIM_LUA``) 一步完成 ZRANGEBYSCORE+ZREM+HSET,把 claim 出的
  payload 挪到 ``{ns}:retry:processing`` hash(field=payload, value=claim_ms);
  处理成功后 ``HDEL``。
- 崩溃恢复:每轮 tick 先扫 processing hash,把 claim 超过
  ``RETRY_PROCESSING_TIMEOUT_SEC``(默认 60s)的条目 requeue 回 ZSet,防止
  claim 后崩溃导致的补派丢失。
- Master 重启/切主:ZSet 数据还在 Redis,新的 Leader 直接从 Redis 拿。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from loguru import logger
from redis.exceptions import NoScriptError

from antcode_core.application.services.scheduler.manual_retry_outbox import get_manual_retry_event
from antcode_core.application.services.scheduler.manual_retry_service import execute_manual_retry
from antcode_core.application.services.scheduler.outbox_service import scheduler_outbox_service
from antcode_core.application.services.scheduler.retry_statistics import build_retry_stats
from antcode_core.common.config import settings
from antcode_core.domain.models.task_run import TaskRun

# ---------------------------------------------------------------------------
# Lua: 原子 claim —— 一步完成 ZRANGEBYSCORE + ZREM + HSET(processing)
# ---------------------------------------------------------------------------
# KEYS[1] = pending ZSet
# KEYS[2] = processing Hash
# ARGV[1] = now_ms (int, upper bound for ZRANGEBYSCORE)
# ARGV[2] = limit  (int, LIMIT count)
# ARGV[3] = claim_ms (int, HSET value = now_ms, used for stale detection)
#
# 返回: due 的 payload 数组([payload, payload, ...]),原子拿走且已挂 processing。
# 崩溃时下轮 sweep_stalled 会从 processing hash 里 requeue。
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

    #: claim 后 N 秒还没 ack 视为崩溃,下轮 sweep 会 requeue
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

    # ------- ops -------
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
        """原子 claim: ZRANGEBYSCORE+ZREM+HSET processing。"""
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
        except NoScriptError:
            # 必须捕获类型：redis-py 已剥掉 "NOSCRIPT" 错误码前缀，按子串判断永不成立。
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
        out: list[dict[str, Any]] = []
        for raw in raw_list or []:
            item = self._decode(raw)
            if item is not None:
                # 原始 payload 字符串,供 ack/requeue 用
                item["__raw_payload"] = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
                out.append(item)
        return out

    async def ack(self, raw_payload: str) -> None:
        """处理成功 -> 从 processing hash 移除。"""
        redis = await self._get_redis()
        removed = await redis.hdel(self.processing_key(), raw_payload)
        if int(removed or 0) != 1:
            raise RuntimeError("retry ack did not remove the processing entry")

    async def requeue(self, raw_payload: str, *, delay_seconds: int = 0) -> None:
        """重新排回 pending(用于处理失败重排 / 崩溃恢复)。"""
        redis = await self._get_redis()
        score = int((time.time() + max(0, delay_seconds)) * 1000)
        pipe = redis.pipeline(transaction=True)
        pipe.zadd(self.pending_key(), {raw_payload: score})
        pipe.hdel(self.processing_key(), raw_payload)
        results = await pipe.execute()
        if len(results) < 2 or int(results[1] or 0) != 1:
            raise RuntimeError("retry requeue did not clear the processing entry")

    async def sweep_stalled(self) -> int:
        """扫 processing hash,把 claim 超时的条目 requeue 回 ZSet。

        崩溃恢复路径:Master claim 出条目后进程炸掉 -> processing hash 里的
        entry 永远不会被 HDEL -> 下轮 tick sweep_stalled 会把它们捞回来。
        """
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
            raw_str = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
            # 立即到期,直接 score = now_ms
            await self.requeue(raw_str, delay_seconds=0)
            requeued += 1
        if requeued:
            logger.warning(f"retry sweep: 从 processing hash 恢复 {requeued} 条崩溃遗留任务")
        return requeued

    async def cancel(self, run_id: str) -> int:
        """按 run_id 从 pending 里移除(cancel 支持)。"""
        redis = await self._get_redis()
        raw_members = await redis.zrange(self.pending_key(), 0, -1)
        removed = 0
        for raw in raw_members or []:
            item = self._decode(raw)
            if item is None:
                continue
            if item.get("run_id") == run_id:
                await redis.zrem(self.pending_key(), raw)
                removed += 1
        return removed


class RetryService:
    """任务重试服务"""

    def __init__(self):
        self._backend = _RetryQueueBackend()
        self._running = False
        self._worker_task: asyncio.Task | None = None

    async def start(self):
        """启动重试服务"""
        self._running = True
        self._worker_task = asyncio.create_task(self._drain_due_retries())
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

    async def manual_retry(self, run_id, user_id):
        """手动重试通过事务服务创建新 run，历史 run 保持不可变。"""
        return await execute_manual_retry(
            run_id,
            user_id,
            cancel_pending=self._backend.cancel,
            enqueue_event=scheduler_outbox_service.enqueue,
            get_event=get_manual_retry_event,
        )

    async def cancel_pending(self, run_id: str) -> int:
        """把待重试意图从 Redis pending 队列移除（配合 DB 清 next_retry_at）。"""
        return await self._backend.cancel(run_id)

    async def _drain_due_retries(self):
        """处理重试队列 —— 从 Redis ZSet 原子 claim,处理成功后 ack。

        P1-18: 每轮先 sweep_stalled 恢复崩溃遗留,再 claim 到期任务。
        Master 重启/切主后 ZSet 数据仍在 Redis,新 Leader 直接接管。
        """
        # 崩溃恢复的扫描间隔(每 N 轮 tick 扫一次,避免每秒都 HGETALL)
        sweep_every_n = 10
        tick_counter = 0

        while self._running:
            try:
                tick_counter += 1
                if tick_counter % sweep_every_n == 1:
                    # 崩溃恢复: 把 processing hash 里超时的 claim requeue
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
                        from antcode_core.application.services.scheduler import (
                            scheduler_service,
                        )

                        await scheduler_service.trigger_task(task_id)
                        logger.info(f"任务 {task_id} 重试已触发")
                        await self._backend.ack(raw_payload)
                    except Exception as exc:
                        # trigger 失败: requeue 一次(带短延迟),留到下轮
                        logger.error(f"trigger_task({task_id}) 失败,requeue: {exc}")
                        await self._backend.requeue(raw_payload, delay_seconds=5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"处理重试队列失败: {e}")
                await asyncio.sleep(1)

    async def get_retry_stats(self, task_id):
        """获取任务重试统计"""
        executions = await TaskRun.filter(task_id=task_id).all()
        return build_retry_stats(task_id, executions)

    async def get_pending_retries(self):
        """从 PostgreSQL 权威 durable intent 获取待重试列表。"""
        from antcode_core.application.services.scheduler.retry_pending_query import (
            list_durable_pending_retries,
        )

        return await list_durable_pending_retries()


retry_service = RetryService()
