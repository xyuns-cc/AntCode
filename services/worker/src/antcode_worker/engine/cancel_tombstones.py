"""FN-01(c): 取消先于任务到达时的本地 tombstone。

control 与 task 是两条流，取消消息可能跑赢任务消息。未命中本地 state
的取消记录为短 TTL tombstone；任务随后到达时在 poll 准入被拦截并按
CANCELLED 结算。TTL 覆盖派发重投窗口即可，插入时顺带清扫过期项。

Round6 P1-SM-03 (5.2 状态机): 加 Redis 持久化备份层, 防 Worker 重启后
丢失 fence, task 消息重投时被误执行。
- record: 内存写 + Redis SET(EX 600s)
- consume: 内存查, miss 则查 Redis GETDEL (原子, 幂等)
- Redis 无 client 时降级纯内存(Gateway 模式无 Redis 凭据 / 单元测试兼容)

键布局按 ``{ns}`` 命名空间 + ``{wid}`` Worker 维度收敛, 与最小权限 Redis
ACL selector 对齐(见 ``redis_acl_policy.ACL_SELECTOR_SPECS``)。无 Worker
维度的共享 ``cancel:tombstone:*`` 写面会让任一 Worker GETDEL 掉其他 Worker
的 fence——取消过的 run 会被照常执行, 与 spider data / run ownership 的
既有顾虑同类, 因此不做共享授权。fence 的实际跨进程场景是同一 worker_id
重启后重投(ready stream 与 control stream 都是 per-worker), 该场景完整保留。
"""

from __future__ import annotations

import asyncio
from typing import Any

from antcode_core.infrastructure.redis import cancel_tombstone_key, validate_worker_key_segment
from loguru import logger

CANCEL_TOMBSTONE_TTL_SECONDS = 600.0


class CancelTombstones:
    """run_id → 过期时刻 的有界 tombstone 集(可选 Redis 持久化)。"""

    def __init__(
        self,
        ttl_seconds: float = CANCEL_TOMBSTONE_TTL_SECONDS,
        *,
        redis_client: Any = None,
        namespace: str | None = None,
        worker_id: str = "",
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._entries: dict[str, float] = {}
        # P1-SM-03 (round6): Redis 备份, 无 client 时纯内存(Gateway/单测)
        self._redis = redis_client
        self._namespace = namespace
        # 键必须落进本 Worker 的 ACL 面; worker_id 缺失或非法时立即失败,
        # 不静默退化成跨 Worker 共享的 tombstone 键
        if redis_client is not None:
            validate_worker_key_segment(worker_id)
        self._worker_id = worker_id

    def _redis_key(self, run_id: str) -> str:
        return cancel_tombstone_key(self._worker_id, run_id, namespace=self._namespace)

    async def record(self, run_id: str, reason: str) -> None:
        now = asyncio.get_event_loop().time()
        self._entries = {key: deadline for key, deadline in self._entries.items() if deadline > now}
        self._entries[run_id] = now + self._ttl_seconds
        logger.info(f"取消先于任务到达，记录 tombstone: run_id={run_id} reason={reason}")
        # P1-SM-03: Redis 备份, 失败不阻塞本地 fence(仍有内存兜底)
        if self._redis is not None:
            try:
                await self._redis.set(self._redis_key(run_id), "1", ex=int(self._ttl_seconds))
            except Exception as exc:  # noqa: BLE001 - 备份失败不能拖住取消路径
                logger.warning("tombstone Redis 备份失败 (仅内存 fence): run_id={} err={}", run_id, exc)

    async def consume(self, run_id: str) -> bool:
        # 内存命中直接 pop
        deadline = self._entries.pop(run_id, None)
        if deadline is not None and deadline > asyncio.get_event_loop().time():
            # P1-SM-03: 同步删 Redis 备份(内存已消费, 避免双消费)
            if self._redis is not None:
                try:
                    await self._redis.delete(self._redis_key(run_id))
                except Exception as exc:  # noqa: BLE001
                    logger.debug("tombstone Redis 清理失败 (内存已消费): run_id={} err={}", run_id, exc)
            return True
        # P1-SM-03: 内存未命中查 Redis (Worker 重启 / 跨 Worker 场景)
        if self._redis is not None:
            try:
                # GETDEL 原子取值 + 删, 保证 tombstone 只被消费一次
                value = await self._redis.getdel(self._redis_key(run_id))
                if value is not None:
                    logger.info("从 Redis 命中 tombstone (Worker 重启后 fence 幸存): run_id={}", run_id)
                    return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("tombstone Redis 查询失败 (仅内存兜底): run_id={} err={}", run_id, exc)
        return False


__all__ = ["CANCEL_TOMBSTONE_TTL_SECONDS", "CancelTombstones"]
