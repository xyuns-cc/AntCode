"""Gateway 任务结算（ACK / requeue / DLQ）—— 代际 consumer fence。

从 ``poll.py`` 拆出的 ``TaskPollHandler`` 结算面：P1-GW-01/02 的两个
结算 Lua 与 outcome 判定。依赖宿主提供 ``_get_redis_client`` 与
``WORKER_GROUP``。
"""

from __future__ import annotations

from datetime import datetime

from antcode_core.infrastructure.redis import trim_acknowledged_stream
from loguru import logger

# P1-GW-03 / P0-03a: legacy 边界判定拆到独立子模块,主文件保持在 300 行内。
from antcode_gateway.handlers._settle_legacy_boundary import (
    legacy_settle_argv as _legacy_settle_argv,
)


class TaskSettlementMixin:
    """任务结算混入：代际 consumer 校验 + 幂等 ACK/requeue/DLQ。"""

    # 宿主（TaskPollHandler）提供的契约面。
    WORKER_GROUP: str

    async def _get_redis_client(self):  # pragma: no cover - 宿主实现
        raise NotImplementedError

    @staticmethod
    def generation_consumer(worker_id: str, lease_id: str) -> str:
        """代际作用域 consumer 名（P1-GW-01）。

        task ready stream（无 hash tag）与 Lease key（``{ns}`` slot）在
        Cluster 下不同 slot，结算 Lua 无法原子读 Lease。改为把代际编进
        consumer 名：新代际 XAUTOCLAIM 接管后 PEL entry 的 consumer 变更，
        旧代际的结算在 Lua 内因 consumer 不匹配被原子拒绝——互斥全部落在
        队列自身 slot 内，不再依赖跨 slot 的 check-then-act。
        """
        if not lease_id:
            raise ValueError("lease_id 不能为空，无法构造代际 consumer")
        return f"{worker_id}:{lease_id}"

    # P1-GW-01: ACK 也收敛为 Lua —— XPENDING 的 consumer 归属校验与 XACK
    # 原子完成。持有 entry 的 consumer 是"当前代际"（XAUTOCLAIM 转移后
    # 变更），旧代际的 ACK 在这里被原子拒绝，无法删除新代际在途的消息。
    # legacy consumer（升级前的裸 worker_id）仅在滚动升级窗口内放行，
    # 仍限定同一 worker。
    _ACK_SETTLE_LUA = """
local queue = KEYS[1]
local group = ARGV[1]
local msg_id = ARGV[2]
local expected = ARGV[3]
local legacy = ARGV[4]

local pending = redis.call('XPENDING', queue, group, msg_id, msg_id, 1)
if #pending == 0 then
    return 'already_settled'
end
local holder = pending[1][2]
if holder ~= expected and holder ~= legacy then
    return 'not_owner'
end
redis.call('XACK', queue, group, msg_id)
return 'acked'
"""

    ACK_OUTCOME_NOT_OWNER = "not_owner"

    async def ack_task(self, worker_id: str, queue: str, message_id: str, *, lease_id: str) -> str:
        """结算 ACK；返回 outcome 字符串（acked/already_settled/not_owner/error）。"""
        redis = await self._get_redis_client()
        if redis is None:
            return "error"

        consumer = self.generation_consumer(worker_id, lease_id)
        legacy_arg = _legacy_settle_argv(worker_id)
        try:
            result = await redis.eval(
                self._ACK_SETTLE_LUA,
                1,
                queue,
                self.WORKER_GROUP,
                message_id,
                consumer,
                legacy_arg,
            )
        except Exception as exc:
            logger.exception(f"确认任务失败: {exc}")
            return "error"

        outcome = result.decode() if isinstance(result, bytes) else str(result)
        if outcome == "already_settled":
            # 幂等：消息已不在 PEL —— 上一次 ACK 已生效但响应丢失（worker
            # 重试）。receipt 的 stream 归属已在 RPC 入口校验过。
            logger.info(f"任务 ACK 幂等命中(已结算): worker_id={worker_id} queue={queue} message_id={message_id}")
        elif outcome == self.ACK_OUTCOME_NOT_OWNER:
            logger.warning(f"拒绝非当前代际的任务 ACK: worker_id={worker_id} queue={queue} message_id={message_id}")
        elif outcome == "acked":
            try:
                await trim_acknowledged_stream(redis, queue, self.WORKER_GROUP)
            except Exception:
                logger.exception(
                    "任务 ACK 后裁剪失败: worker_id={} queue={}",
                    worker_id,
                    queue,
                )
            logger.debug(f"任务已确认: worker_id={worker_id}, queue={queue}, message_id={message_id}")
        return outcome

    async def ack_receipt(
        self,
        receipt_id: str,
        accepted: bool = True,
        reason: str = "",
        *,
        worker_id: str,
        lease_id: str,
    ) -> str:
        """确认任务（receipt 形式）；返回 outcome 字符串。"""
        if "|" not in receipt_id:
            return "error"
        queue, message_id = receipt_id.split("|", 1)
        if accepted:
            return await self.ack_task(worker_id, queue, message_id, lease_id=lease_id)
        return await self._requeue_task(queue, message_id, reason, worker_id=worker_id, lease_id=lease_id)

    def is_settle_success(self, outcome: str) -> bool:
        """结算 outcome 是否可对 worker 报成功（含幂等命中与终局裁决）。"""
        return outcome in self._SETTLE_SUCCESS_OUTCOMES

    # B7: 与 Direct 模式（redis/transport.py:MAX_REQUEUE_COUNT）对齐；
    # 超阈值的消息进入死信 stream 而非无限重投。
    MAX_REQUEUE_COUNT = 5
    DEAD_LETTER_STREAM_SUFFIX = "task:dead_letter"
    DEAD_LETTER_MAXLEN = 10000

    # P1-GW-02: requeue/DLQ 结算收敛为单个 Lua —— XPENDING 幂等守卫 +
    # XRANGE + XADD + XACK 原子完成。此前 XADD 成功、XACK 失败（或响应
    # 丢失后重试）会重复注入同一任务；现在重试命中 already_settled，
    # 不再产生重复消息。
    # P1-GW-01: 增加 consumer 代际归属校验（ARGV[7]/ARGV[8]），旧代际
    # 无法 requeue/DLQ 新代际在途的消息。
    _REQUEUE_SETTLE_LUA = """
local queue = KEYS[1]
local dlq = KEYS[2]
local group = ARGV[1]
local msg_id = ARGV[2]
local reason = ARGV[3]
local now_iso = ARGV[4]
local max_requeue = tonumber(ARGV[5])
local dlq_maxlen = tonumber(ARGV[6])
local expected = ARGV[7]
local legacy = ARGV[8]

local pending = redis.call('XPENDING', queue, group, msg_id, msg_id, 1)
if #pending == 0 then
    return 'already_settled'
end
if pending[1][2] ~= expected and pending[1][2] ~= legacy then
    return 'not_owner'
end
local msgs = redis.call('XRANGE', queue, msg_id, msg_id)
if #msgs == 0 then
    redis.call('XACK', queue, group, msg_id)
    return 'source_missing'
end
local fields = msgs[1][2]
local payload = {}
local count = 0
for i = 1, #fields, 2 do
    local k = fields[i]
    local v = fields[i + 1]
    if k == 'requeue_count' then
        count = tonumber(v) or 0
    elseif k ~= 'requeue_reason' and k ~= 'requeue_at' and k ~= 'last_requeue_reason'
        and k ~= 'dead_letter_at' and k ~= 'origin_queue' then
        payload[#payload + 1] = k
        payload[#payload + 1] = v
    end
end
count = count + 1
if count > max_requeue then
    payload[#payload + 1] = 'requeue_count'
    payload[#payload + 1] = tostring(count)
    payload[#payload + 1] = 'last_requeue_reason'
    payload[#payload + 1] = reason
    payload[#payload + 1] = 'dead_letter_at'
    payload[#payload + 1] = now_iso
    payload[#payload + 1] = 'origin_queue'
    payload[#payload + 1] = queue
    redis.call('XADD', dlq, 'MAXLEN', '~', dlq_maxlen, '*', unpack(payload))
    redis.call('XACK', queue, group, msg_id)
    return 'dead_lettered'
end
payload[#payload + 1] = 'requeue_count'
payload[#payload + 1] = tostring(count)
payload[#payload + 1] = 'requeue_reason'
payload[#payload + 1] = reason
payload[#payload + 1] = 'requeue_at'
payload[#payload + 1] = now_iso
redis.call('XADD', queue, '*', unpack(payload))
redis.call('XACK', queue, group, msg_id)
return 'requeued'
"""

    def _dead_letter_key(self, queue: str) -> str:
        """per-queue DLQ key，与源队列同 slot（复审 D3）。

        `_REQUEUE_SETTLE_LUA` 同时访问 queue 与 DLQ 两个 key；全局
        `<ns>:task:dead_letter` 与 per-worker queue 在 Redis Cluster 下
        不同 slot，EVAL 必抛 CROSSSLOT → requeue/DLQ 通道整体不可用。
        与 Direct 侧 task_settlement 的 co-slot marker 同一技术。
        """
        from antcode_core.infrastructure.redis.cluster_slots import co_slot_hash_tag

        return f"{queue}:{{{co_slot_hash_tag(queue, 'dlq')}}}:{self.DEAD_LETTER_STREAM_SUFFIX}"

    _SETTLE_SUCCESS_OUTCOMES = frozenset({"acked", "requeued", "dead_lettered", "already_settled", "source_missing"})

    async def _requeue_task(
        self,
        queue: str,
        message_id: str,
        reason: str,
        *,
        worker_id: str,
        lease_id: str,
    ) -> str:
        """拒绝任务并回写 ready stream；超过 ``MAX_REQUEUE_COUNT`` 次进死信。

        注：保留 JSON 帧以兼容 Master 当前的 ``_send_batch_to_queue`` 写入端。
        待 Master 切换为 ``ProtoCodec(TaskDispatch)`` 派发后，这里需要改为
        ``xadd {PROTO_FIELD: TaskDispatch.SerializeToString()}``。
        """
        redis = await self._get_redis_client()
        if redis is None:
            return "error"

        consumer = self.generation_consumer(worker_id, lease_id)
        legacy_arg = _legacy_settle_argv(worker_id)
        try:
            result = await redis.eval(
                self._REQUEUE_SETTLE_LUA,
                2,
                queue,
                self._dead_letter_key(queue),
                self.WORKER_GROUP,
                message_id,
                reason,
                datetime.now().isoformat(),
                str(self.MAX_REQUEUE_COUNT),
                str(self.DEAD_LETTER_MAXLEN),
                consumer,
                legacy_arg,
            )
        except Exception as exc:
            logger.exception(f"重新入队失败: {exc}")
            return "error"

        outcome = result.decode() if isinstance(result, bytes) else str(result)
        if outcome == "already_settled":
            logger.info(f"requeue 幂等命中(已结算): queue={queue} msg={message_id}")
        elif outcome == self.ACK_OUTCOME_NOT_OWNER:
            logger.warning(f"拒绝非当前代际的 requeue: queue={queue} msg={message_id} worker={worker_id}")
        elif outcome == "source_missing":
            logger.warning(f"requeue 源消息已被 trim，已清 PEL: queue={queue} msg={message_id}")
        elif outcome == "dead_lettered":
            logger.warning(
                f"消息进入死信(超过 {self.MAX_REQUEUE_COUNT} 次 requeue): queue={queue} msg={message_id} reason={reason}"
            )
        return outcome
