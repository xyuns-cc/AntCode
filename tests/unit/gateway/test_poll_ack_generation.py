"""P1-GW-01/02：TaskPollHandler ACK 代际结算（_ACK_SETTLE_LUA 脚本合同）测试。"""

import pytest
from antcode_core.infrastructure.redis import task_ready_stream
from antcode_gateway.handlers.poll import TaskPollHandler


class _AckSettleRedis:
    """模拟 _ACK_SETTLE_LUA：按脚本合同返回 outcome。"""

    def __init__(self, pel: dict[str, str] | None = None):
        self.pel = dict(pel or {})
        self.acked: list[str] = []

    async def eval(self, script, numkeys, *rest):
        keys = rest[:numkeys]
        argv = rest[numkeys:]
        assert numkeys == 1
        _group, msg_id, expected = argv
        holder = self.pel.get(msg_id)
        if holder is None:
            return b"already_settled"
        if holder != expected:
            return b"not_owner"
        del self.pel[msg_id]
        self.acked.append(msg_id)
        return b"acked"

    async def xrange(self, *args, **kwargs):
        return []


@pytest.mark.asyncio
async def test_ack_task_treats_missing_pel_entry_as_idempotent_success():
    # P1-GW-02: 消息已不在 PEL（上一次 ACK 已生效但响应丢失）→ 幂等成功，
    # 避免 worker 对已结算消息永久重试 ACK。
    handler = TaskPollHandler(redis_client=_AckSettleRedis())

    outcome = await handler.ack_task(
        worker_id="worker-1",
        queue=task_ready_stream("worker-1"),
        message_id="already-settled-id",
        lease_id="lease-1",
    )

    assert outcome == "already_settled"
    assert handler.is_settle_success(outcome) is True


@pytest.mark.asyncio
async def test_ack_task_current_generation_consumer_acks():
    redis = _AckSettleRedis(pel={"1-0": "worker-1:lease-1"})
    handler = TaskPollHandler(redis_client=redis)

    outcome = await handler.ack_task(
        worker_id="worker-1",
        queue=task_ready_stream("worker-1"),
        message_id="1-0",
        lease_id="lease-1",
    )

    assert outcome == "acked"
    assert redis.acked == ["1-0"]


@pytest.mark.asyncio
async def test_ack_task_rejects_stale_generation_consumer():
    # P1-GW-01: entry 已被新代际 consumer 接管，旧代际 ACK 原子拒绝。
    redis = _AckSettleRedis(pel={"1-0": "worker-1:lease-new"})
    handler = TaskPollHandler(redis_client=redis)

    outcome = await handler.ack_task(
        worker_id="worker-1",
        queue=task_ready_stream("worker-1"),
        message_id="1-0",
        lease_id="lease-old",
    )

    assert outcome == TaskPollHandler.ACK_OUTCOME_NOT_OWNER
    assert handler.is_settle_success(outcome) is False
    assert redis.acked == []


@pytest.mark.asyncio
async def test_ack_task_rejects_bare_worker_consumer():
    """裸 worker_id（无代际）不再是可结算的 consumer 名。

    它只在滚动升级窗口内有意义；停机窗口上线后 PEL 里不可能出现这种 entry，
    再放行等于给旧代际留一条绕开代际 fence 的路。
    """
    redis = _AckSettleRedis(pel={"1-0": "worker-1"})
    handler = TaskPollHandler(redis_client=redis)

    outcome = await handler.ack_task(
        worker_id="worker-1",
        queue=task_ready_stream("worker-1"),
        message_id="1-0",
        lease_id="lease-1",
    )

    assert outcome == TaskPollHandler.ACK_OUTCOME_NOT_OWNER
    assert redis.acked == []
