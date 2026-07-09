"""T7-B3a (P1-1): 派发失败自动补派服务。

**为什么和 RetryService 分开**：
- ``RetryService`` 覆盖的是"任务已经派出去并跑完但结果 FAILED"的场景，重试
  是让 worker 再跑一次；且它是进程内 ``asyncio.Queue``，master 重启即丢。
- 本 service 覆盖的是"派发调用本身就失败"（无可用 worker / worker 未在线 /
  gRPC 断连）的场景，此前 run 直接置 FAILED 永远不会被再次派发——这是审
  查报告的 P1-1。
- 补派队列用 Redis ZSet 持久化，master 重启后仍能捡起继续派发。

**队列**：``{namespace}:task:redispatch`` （ZSet）
- member = JSON payload（run_id + task_id + project_id + params + attempts）
- score = next_attempt_ms（epoch 毫秒）

**流程**：
1. dispatch 失败调 ``enqueue(...)`` 落 ZSet，score = now + backoff
2. master ``RedispatchLoop`` 每 10s tick，``pop_due()`` 拿到期项目
3. 逐个重派：成功即完；仍失败则 attempts++ 重新入队；超阈值触发放弃回调
4. 放弃时 status=FAILED + audit_log + 告警（调用方注入回调）

**指数退避**：``base * 2^attempts`` capped 到 max_delay，默认 30s..300s。
"""

from __future__ import annotations

import json
import time
from typing import Any

from loguru import logger

from antcode_core.common.config import settings
from antcode_core.infrastructure.redis.client import get_redis_client


class RedispatchService:
    """派发失败任务的持久化补派队列。"""

    #: 单条任务最多补派次数（含首次派发）；超阈值触发放弃回调
    DEFAULT_MAX_ATTEMPTS = 5
    #: 指数退避基数与上限（秒）
    DEFAULT_BASE_DELAY = 30
    DEFAULT_MAX_DELAY = 300

    def __init__(self) -> None:
        self._namespace = settings.REDIS_NAMESPACE
        self._max_attempts = int(
            getattr(settings, "REDISPATCH_MAX_ATTEMPTS", self.DEFAULT_MAX_ATTEMPTS)
        )
        self._base_delay = int(
            getattr(settings, "REDISPATCH_BASE_DELAY_SEC", self.DEFAULT_BASE_DELAY)
        )
        self._max_delay = int(
            getattr(settings, "REDISPATCH_MAX_DELAY_SEC", self.DEFAULT_MAX_DELAY)
        )

    # -----------------------------------------------------------------

    def _key(self) -> str:
        return f"{self._namespace}:task:redispatch"

    def next_delay_seconds(self, attempts: int) -> int:
        """指数退避：base * 2^attempts，capped。"""
        if attempts <= 0:
            return self._base_delay
        delay = self._base_delay * (2 ** min(attempts, 10))  # 防止溢出
        return min(delay, self._max_delay)

    def should_give_up(self, attempts: int) -> bool:
        return attempts >= self._max_attempts

    # -----------------------------------------------------------------

    async def enqueue(
        self,
        *,
        run_id: str,
        task_id: int | None = None,
        project_id: str,
        params: dict[str, Any] | None = None,
        environment_vars: dict[str, str] | None = None,
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
                f"补派放弃: run_id={run_id} attempts={attempts} 已超上限 "
                f"{self._max_attempts}，交调用方置 FAILED"
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
        # 但我们希望"attempts 递增后"是新的 member（reason 会变），实际 payload 不同→天然区分
        await redis.zadd(self._key(), {payload: score})
        logger.info(
            f"补派入队: run_id={run_id} attempts={attempts + 1}/{self._max_attempts} "
            f"delay={delay}s reason={reason!r}"
        )
        return True

    async def pop_due(self, limit: int = 50) -> list[dict[str, Any]]:
        """拿出所有到期（score <= now）的 payload，并从 ZSet 移除。

        用 ZRANGEBYSCORE + ZREM 分两步（非原子），最坏 fallout 是一条被两个
        master 实例同时捞到——但补派 loop 是 leader-only，只有一个实例扫，
        所以并发只在"leader 切换瞬间"存在，可接受。
        """
        redis = await get_redis_client()
        now_ms = int(time.time() * 1000)
        members = await redis.zrangebyscore(
            self._key(), min=0, max=now_ms, start=0, num=limit
        )
        if not members:
            return []
        # 批量移除
        await redis.zrem(self._key(), *members)
        out: list[dict[str, Any]] = []
        for m in members:
            if isinstance(m, bytes):
                m = m.decode("utf-8", errors="ignore")
            try:
                out.append(json.loads(m))
            except json.JSONDecodeError as exc:
                logger.warning(f"补派 payload 反序列化失败，丢弃: {exc}")
        return out

    async def pending_count(self) -> int:
        redis = await get_redis_client()
        return int(await redis.zcard(self._key()))


redispatch_service = RedispatchService()


__all__ = ["RedispatchService", "redispatch_service"]
