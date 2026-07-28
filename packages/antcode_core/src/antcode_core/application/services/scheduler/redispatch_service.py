"""T7-B3a (P1-1): 派发失败自动补派服务。

**为什么和 RetryService 分开**：
- ``RetryService`` 覆盖的是"任务已经派出去并跑完但结果 FAILED"的场景,重试
  是让 worker 再跑一次。
- 本 service 覆盖的是"派发调用本身就失败"（无可用 worker / worker 未在线 /
  gRPC 断连）的场景,此前 run 直接置 FAILED 永远不会被再次派发 —— 这是审
  查报告的 P1-1。
- 补派队列用 Redis ZSet 持久化,master 重启后仍能捡起继续派发。

**队列**：``{namespace}:task:redispatch`` （ZSet）
- member = JSON payload（run_id + task_id + project_id + params + attempts）
- score = next_attempt_ms（epoch 毫秒）

**流程**：
1. dispatch 失败调 ``enqueue(...)`` 落 ZSet,score = now + backoff
2. master ``RedispatchLoop`` 每 10s tick,``claim_due()`` 拿到期项目
3. 逐个重派：成功即 ``ack``；仍失败则 attempts++ 重新入队；超阈值触发放弃回调
4. 放弃时 status=FAILED + audit_log + 告警（调用方注入回调）

**指数退避**：``base * 2^attempts`` capped 到 max_delay,默认 30s..300s。

**P1-18 修复**:原实现 ``pop_due()`` 分开 ZRANGEBYSCORE + ZREM 两步,ZREM
执行后 handler 抛异常或进程崩溃 -> payload 丢失,补派永久失败。切主瞬间
两个实例同时扫也会重复处理。

现在:
- ``claim_due()`` 用 Lua 原子完成 ZRANGEBYSCORE + ZREM + HSET(processing),
  一步内挪到 ``{namespace}:task:redispatch:processing`` hash。
- handler 成功 -> ``ack(raw_payload)`` HDEL processing;
- handler 失败或 attempts++ 重新入队时,先 enqueue 新 payload 再 ack 旧
  raw_payload,保证不丢单;
- 崩溃恢复:每轮 tick 先 ``sweep_stalled()``,把 processing hash 里 claim
  超过 ``REDISPATCH_PROCESSING_TIMEOUT_SEC``(默认 120s)的条目 requeue。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable
from typing import Any, cast

from loguru import logger

from antcode_core.common.config import settings
from antcode_core.infrastructure.redis.client import get_redis_client

# ---------------------------------------------------------------------------
# Lua: 原子 claim —— ZRANGEBYSCORE + ZREM + HSET processing 一步完成
# ---------------------------------------------------------------------------
# KEYS[1] = pending ZSet, KEYS[2] = processing Hash
# ARGV[1] = now_ms, ARGV[2] = limit, ARGV[3] = claim_ms(processing value)
_REDISPATCH_CLAIM_LUA = """
local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, tonumber(ARGV[2]))
if #due == 0 then return {} end
redis.call('ZREM', KEYS[1], unpack(due))
for i, payload in ipairs(due) do
    redis.call('HSET', KEYS[2], payload, ARGV[3])
end
return due
"""


class RedispatchService:
    """派发失败任务的持久化补派队列。"""

    #: 单条任务最多补派次数（含首次派发）；超阈值触发放弃回调
    DEFAULT_MAX_ATTEMPTS = 5
    #: 指数退避基数与上限（秒）
    DEFAULT_BASE_DELAY = 30
    DEFAULT_MAX_DELAY = 300
    #: processing hash 里 claim 超过 N 秒还没 ack 视为崩溃,下轮 sweep 恢复
    DEFAULT_PROCESSING_TIMEOUT_SEC = 120

    def __init__(self) -> None:
        self._namespace = settings.REDIS_NAMESPACE
        self._max_attempts = int(getattr(settings, "REDISPATCH_MAX_ATTEMPTS", self.DEFAULT_MAX_ATTEMPTS))
        self._base_delay = int(getattr(settings, "REDISPATCH_BASE_DELAY_SEC", self.DEFAULT_BASE_DELAY))
        self._max_delay = int(getattr(settings, "REDISPATCH_MAX_DELAY_SEC", self.DEFAULT_MAX_DELAY))
        self._processing_timeout_sec = int(
            getattr(
                settings,
                "REDISPATCH_PROCESSING_TIMEOUT_SEC",
                self.DEFAULT_PROCESSING_TIMEOUT_SEC,
            )
        )
        self._claim_sha: str | None = None
        self._script_lock = asyncio.Lock()

    # -----------------------------------------------------------------

    def _key(self) -> str:
        return f"{self._namespace}:task:redispatch"

    def _processing_key(self) -> str:
        return f"{self._namespace}:task:redispatch:processing"

    def next_delay_seconds(self, attempts: int) -> int:
        """指数退避：base * 2^attempts,capped。"""
        if attempts <= 0:
            return self._base_delay
        delay = self._base_delay * (2 ** min(attempts, 10))  # 防止溢出
        return min(delay, self._max_delay)

    def should_give_up(self, attempts: int) -> bool:
        return attempts >= self._max_attempts

    async def _ensure_script(self, redis) -> None:
        if self._claim_sha is not None:
            return
        async with self._script_lock:
            if self._claim_sha is None:
                self._claim_sha = await redis.script_load(_REDISPATCH_CLAIM_LUA)

    # -----------------------------------------------------------------

    async def enqueue(
        self,
        *,
        run_id: str,
        task_id: int | None = None,
        project_id: str,
        params: dict[str, Any] | None = None,
        environment_vars: dict[str, str] | None = None,
        runtime_env_name: str | None = None,
        timeout: int = 3600,
        project_type: str = "code",
        attempts: int = 0,
        reason: str = "",
    ) -> bool:
        """把一次失败派发挂入补派队列。

        返回 True 表示已入队；False 表示超阈值放弃（调用方应走 FAILED 落地）。
        """
        if self.should_give_up(attempts):
            logger.warning(
                f"补派放弃: run_id={run_id} attempts={attempts} 已超上限 {self._max_attempts},交调用方置 FAILED"
            )
            return False

        redis = await get_redis_client()
        payload = json.dumps(
            {
                "run_id": run_id,
                "task_id": task_id,
                "project_id": project_id,
                "params": params or {},
                "environment_vars": environment_vars or {},
                "runtime_env_name": runtime_env_name or "",
                "timeout": int(timeout),
                "project_type": project_type,
                "attempts": int(attempts),
                "reason": reason,
                "enqueued_at_ms": int(time.time() * 1000),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        delay = self.next_delay_seconds(attempts)
        score = int(time.time() * 1000) + delay * 1000
        # ZSet 用 payload 作 member；同 payload 多次入队会被 dedup（同 score 只留一份）
        # 但我们希望"attempts 递增后"是新的 member（reason 会变）,实际 payload 不同→天然区分
        await redis.zadd(self._key(), {payload: score})
        logger.info(
            f"补派入队: run_id={run_id} attempts={attempts + 1}/{self._max_attempts} delay={delay}s reason={reason!r}"
        )
        return True

    async def claim_due(self, limit: int = 50) -> list[dict[str, Any]]:
        """原子拿出到期(score <= now)的 payload —— ZRANGEBYSCORE+ZREM+HSET
        processing 一步完成,崩溃时下轮 ``sweep_stalled()`` 恢复。

        返回的每个 dict 里带 ``__raw_payload`` 字段,供 ``ack()`` /
        ``requeue()`` 使用(processing hash 的 field 是原始 JSON 字符串)。
        """
        redis = await get_redis_client()
        await self._ensure_script(redis)
        claim_sha = self._claim_sha
        if claim_sha is None:
            raise RuntimeError("redispatch claim 脚本未加载")
        now_ms = int(time.time() * 1000)
        try:
            raw_list = await cast(
                Awaitable[list[bytes | str]],
                redis.evalsha(
                    claim_sha,
                    2,
                    self._key(),
                    self._processing_key(),
                    str(now_ms),
                    str(limit),
                    str(now_ms),
                ),
            )
        except Exception as exc:
            if "NOSCRIPT" in str(exc):
                logger.warning("redispatch claim 脚本未在 Redis 缓存中,回退 EVAL")
                self._claim_sha = None
                raw_list = await cast(
                    Awaitable[list[bytes | str]],
                    redis.eval(
                        _REDISPATCH_CLAIM_LUA,
                        2,
                        self._key(),
                        self._processing_key(),
                        str(now_ms),
                        str(limit),
                        str(now_ms),
                    ),
                )
            else:
                raise
        out: list[dict[str, Any]] = []
        for m in raw_list or []:
            raw_str = m.decode("utf-8") if isinstance(m, bytes) else m
            item = json.loads(raw_str)
            if not isinstance(item, dict):
                raise ValueError("redispatch payload 必须是 JSON object")
            item["__raw_payload"] = raw_str
            out.append(item)
        return out

    # 向后兼容: pop_due 保留但走原子 claim + auto-ack;调用方若不再需要
    # ack 语义(processing 追踪)可继续用,但会丢失崩溃恢复保护。
    # 新调用请优先使用 ``claim_due`` + ``ack`` / ``requeue``。
    async def pop_due(self, limit: int = 50) -> list[dict[str, Any]]:
        """兼容旧签名:等价于 ``claim_due`` 后立即 ack 所有条目。

        新代码请改用 ``claim_due`` + ``ack``/``requeue``,以获得崩溃恢复保护。
        """
        due = await self.claim_due(limit=limit)
        for item in due:
            raw = item.pop("__raw_payload", None)
            if raw:
                await self.ack(raw)
        return due

    async def ack(self, raw_payload: str) -> None:
        """处理成功 -> 从 processing hash 移除。"""
        redis = await get_redis_client()
        removed = await cast(Awaitable[int], redis.hdel(self._processing_key(), raw_payload))
        if int(removed or 0) != 1:
            raise RuntimeError("redispatch ack did not remove the processing entry")

    async def requeue_raw(self, raw_payload: str, *, delay_seconds: int = 0) -> None:
        """把已 claim 的原始 payload 放回 ZSet(用于处理异常或崩溃恢复)。

        与 ``enqueue`` 不同:这里保持原 payload 字节完全一致,不改 attempts;
        用于确保重复处理时不会因为 payload 抖动生成多个 ZSet member。
        """
        redis = await get_redis_client()
        score = int((time.time() + max(0, delay_seconds)) * 1000)
        pipe = redis.pipeline(transaction=True)
        pipe.zadd(self._key(), {raw_payload: score})
        pipe.hdel(self._processing_key(), raw_payload)
        results = await pipe.execute()
        if len(results) < 2 or int(results[1] or 0) != 1:
            raise RuntimeError("redispatch requeue did not clear the processing entry")

    async def sweep_stalled(self) -> int:
        """崩溃恢复:把 processing hash 里超时的条目 requeue 回 ZSet。

        Master claim 出 payload 后进程崩溃 -> processing hash 里 entry 永远
        不会被 HDEL -> 下轮 tick 由此方法把它们捞回来。切主时新 Leader 首轮
        tick 也会跑一次,兜住上一任 leader 崩溃留下的漂浮 claim。
        """
        redis = await get_redis_client()
        now_ms = int(time.time() * 1000)
        threshold_ms = now_ms - self._processing_timeout_sec * 1000
        try:
            entries = await cast(
                Awaitable[dict[bytes | str, bytes | str]],
                redis.hgetall(self._processing_key()),
            )
        except Exception as exc:
            logger.warning(f"redispatch sweep hgetall 失败: {exc}")
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
            await self.requeue_raw(raw_str, delay_seconds=0)
            requeued += 1
        if requeued:
            logger.warning(f"redispatch sweep: 从 processing hash 恢复 {requeued} 条崩溃遗留任务")
        return requeued

    async def pending_count(self) -> int:
        redis = await get_redis_client()
        return int(await redis.zcard(self._key()))


redispatch_service = RedispatchService()


__all__ = ["RedispatchService", "redispatch_service"]
