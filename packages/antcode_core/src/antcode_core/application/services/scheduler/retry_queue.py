"""重试队列的 Redis 后端 —— Master 与 web_api 共用的唯一一份实现。

``{ns}:retry:pending`` (ZSet, score=到期毫秒) 是投递通道,
``{ns}:retry:processing`` (Hash, value=claim 毫秒) 支撑崩溃恢复,
``{ns}:retry:attempts`` (Hash) 只记诊断用的连续失败次数。

权威的 retry intent 始终是 ``TaskRun.next_retry_at``;本模块只搬运。
消费(claim/ack/sweep)只发生在 Master 的 Leader 门禁之后,
web_api 侧仅用 ``cancel``。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from loguru import logger
from redis.exceptions import NoScriptError

from antcode_core.common.config import settings

# 原子 claim: ZRANGEBYSCORE + ZREM + HSET processing 一步完成
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

#: requeue 的 pipeline 步数(zadd pending + hdel processing),用于校验两步都执行了
_REQUEUE_PIPELINE_STEPS = 2


class RetryQueueBackend:
    """Redis ZSet 后端 —— schedule / claim / ack / sweep_stalled / cancel"""

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
        raw_list = await self._eval_claim(redis, now_ms, limit)
        out: list[dict[str, Any]] = []
        for raw in raw_list or []:
            # V4: 单条 payload 解码失败不能让整批 claim_due 抛出 —— 否则本批已被
            # Lua 移入 processing 的好条目全部悬空未 ack,sweep_stalled 又会把坏
            # 条目喂回来,永久楔死整条 retry 流水线。坏条目直接从 processing 移除。
            try:
                item = self._decode(raw)
            except Exception as exc:
                logger.error(f"retry claim 解码失败,丢弃坏 payload: raw={raw!r} exc={exc}")
                await self._drop_undecodable(redis, raw)
                continue
            item["__raw_payload"] = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            out.append(item)
        return out

    async def _eval_claim(self, redis, now_ms: int, limit: int):
        args = (2, self.pending_key(), self.processing_key(), str(now_ms), str(limit), str(now_ms))
        try:
            return await redis.evalsha(self._claim_sha, *args)
        except NoScriptError:
            # 必须捕获类型：redis-py 已剥掉 "NOSCRIPT" 错误码前缀，按子串判断永不成立。
            logger.warning("retry claim 脚本未在 Redis 缓存中,回退 EVAL")
            self._claim_sha = None
            return await redis.eval(_RETRY_CLAIM_LUA, *args)

    async def _drop_undecodable(self, redis, raw: bytes | str) -> None:
        try:
            await redis.hdel(self.processing_key(), raw)
        except Exception as exc:
            logger.warning(f"丢弃坏 retry payload 时清理 processing 失败: {exc}")

    async def ack(self, raw_payload: str) -> None:
        redis = await self._get_redis()
        removed = await redis.hdel(self.processing_key(), raw_payload)
        if int(removed or 0) != 1:
            raise RuntimeError("retry ack did not remove the processing entry")

    async def incr_attempts(self, run_id: str) -> int:
        """以 run_id 为键记连续失败次数,独立于 payload 字节。

        仅用于告警和诊断；基础设施故障不会据此删除 durable intent。
        """
        redis = await self._get_redis()
        return int(await redis.hincrby(self.attempts_key(), run_id, 1))

    async def clear_attempts(self, run_id: str) -> None:
        if not run_id:
            return
        redis = await self._get_redis()
        await redis.hdel(self.attempts_key(), run_id)

    async def requeue(self, raw_payload: str, *, delay_seconds: int = 0) -> None:
        """把 processing 中的条目放回 pending。"""
        redis = await self._get_redis()
        score = int((time.time() + max(0, delay_seconds)) * 1000)
        pipe = redis.pipeline(transaction=True)
        pipe.zadd(self.pending_key(), {raw_payload: score})
        pipe.hdel(self.processing_key(), raw_payload)
        results = await pipe.execute()
        if len(results) < _REQUEUE_PIPELINE_STEPS or int(results[1] or 0) != 1:
            raise RuntimeError("retry requeue did not clear the processing entry")

    async def sweep_stalled(self) -> int:
        """崩溃恢复:把 processing hash 里 claim 超时的条目 requeue 回 ZSet。"""
        redis = await self._get_redis()
        threshold_ms = int(time.time() * 1000) - self._processing_timeout_sec * 1000
        try:
            entries = await redis.hgetall(self.processing_key())
        except Exception as exc:
            logger.warning(f"retry sweep hgetall 失败: {exc}")
            return 0
        requeued = 0
        for raw, claim_ms_raw in (entries or {}).items():
            if _claim_millis(claim_ms_raw) > threshold_ms:
                continue
            await self.requeue(raw.decode("utf-8") if isinstance(raw, bytes) else raw, delay_seconds=0)
            requeued += 1
        if requeued:
            logger.warning(f"retry sweep: 从 processing hash 恢复 {requeued} 条崩溃遗留任务")
        return requeued

    async def _scan_pending(self, redis) -> list[tuple[bytes | str, dict[str, Any]]]:
        """扫 pending 全量并解码;坏条目跳过。

        取消类操作不该被一条无关的损坏 payload 打成 500 —— 坏条目由
        ``claim_due`` 在消费侧清理。
        """
        decoded: list[tuple[bytes | str, dict[str, Any]]] = []
        for raw in await redis.zrange(self.pending_key(), 0, -1) or []:
            try:
                decoded.append((raw, self._decode(raw)))
            except ValueError as exc:
                logger.warning(f"retry pending 条目解码失败,跳过: raw={raw!r} exc={exc}")
        return decoded

    async def cancel(self, run_id: str) -> int:
        """按 source run_id 移除 pending 条目(web_api 取消单次重试)。"""
        redis = await self._get_redis()
        matches = [raw for raw, item in await self._scan_pending(redis) if item.get("run_id") == run_id]
        if not matches:
            return 0
        return int(await redis.zrem(self.pending_key(), *matches) or 0)

    async def cancel_task(self, task_id: int) -> int:
        """移除已删除任务的 pending 条目;在途 claim 由 PostgreSQL 侧自我隔离。"""
        redis = await self._get_redis()
        matches = [
            (raw, str(item.get("run_id") or ""))
            for raw, item in await self._scan_pending(redis)
            if _matches_task(item, task_id)
        ]
        if not matches:
            return 0
        pipeline = redis.pipeline(transaction=True)
        pipeline.zrem(self.pending_key(), *(raw for raw, _run_id in matches))
        run_ids = [run_id for _raw, run_id in matches if run_id]
        if run_ids:
            pipeline.hdel(self.attempts_key(), *run_ids)
        results = await pipeline.execute()
        return int(results[0] or 0)


def _claim_millis(raw: bytes | str) -> int:
    """无法解析的 claim 时间戳按 0 处理,即立刻判为超时并 requeue。"""
    try:
        return int(raw.decode() if isinstance(raw, bytes) else raw)
    except (ValueError, AttributeError):
        return 0


def _matches_task(item: dict[str, Any], task_id: int) -> bool:
    raw_task_id = item.get("task_id")
    if raw_task_id is None:
        return False
    try:
        return int(raw_task_id) == task_id
    except (TypeError, ValueError):
        return False


__all__ = ["RetryQueueBackend"]
