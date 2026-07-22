"""Direct Redis control generation, ownership, and JSON boundary tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_contracts import data_pb2
from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD
from antcode_worker.transport.base import TaskResult
from antcode_worker.transport.redis.codecs import CodecError, JsonCodec
from antcode_worker.transport.redis.runtime_control import (
    ControlChannel,
    ControlSource,
    PendingControlRecovery,
    RuntimeControlResult,
)
from antcode_worker.transport.redis.runtime_control_evidence import (
    CONTROL_REPLY_TTL_SECONDS,
    MAX_RUNTIME_ACTION_SECONDS,
    SETTLEMENT_RECOVERY_GRACE_SECONDS,
    settlement_fingerprint,
    settlement_ttl_seconds,
)
from antcode_worker.transport.redis.transport import RedisTransport


@pytest.mark.asyncio
async def test_control_recovery_claims_old_lease_consumer_before_draining_current_pel():
    channel = ControlChannel("antcode:control:worker-1", "antcode-control")
    recovery = PendingControlRecovery(
        (channel,),
        legacy_consumer_name="worker-1",
        page_size=2,
    )
    redis = AsyncMock()
    redis.xpending_range.side_effect = [
        [
            {
                "message_id": "1-0",
                "consumer": "worker-1-lease-1",
                # P1-DR-06: min_idle 从 0 提到 LEASE_TTL/4 = 7500ms,新代际不
                # 会抢占刚投递的旧代际消息;测试用 30_000ms 表明消息已长期
                # 卡在旧代际 PEL(worker 崩溃/长跑取消)才被新代际接管。
                "time_since_delivered": 30_000,
                "times_delivered": 1,
            }
        ],
        [],
        [],
    ]
    redis.eval.return_value = [["1-0", ["control_type", "cancel"]]]
    redis.xreadgroup.return_value = [(channel.stream_key, [("1-0", {"control_type": "cancel"})])]

    delivery = await recovery.poll(redis, "worker-1-lease-2")

    assert delivery == (channel.stream_key, "1-0", {"control_type": "cancel"})
    eval_args = redis.eval.await_args.args
    assert eval_args[1:4] == (1, channel.stream_key, channel.group)
    assert "worker-1-lease-2" in eval_args
    assert "worker-1-lease-1" in eval_args


def _transport(redis: AsyncMock) -> RedisTransport:
    transport = RedisTransport(redis_url="redis://localhost/0", worker_id="worker-1")
    transport._redis = redis
    transport._running = True
    return transport


def _enable_fencing(transport: RedisTransport, *, current: bool) -> AsyncMock:
    is_current = AsyncMock(return_value=current)
    transport._lease_store = SimpleNamespace(is_current=is_current)
    transport._lease_id = "lease-1"
    transport._lease_fencing_enabled = True
    return is_current


@pytest.mark.asyncio
async def test_stale_generation_cannot_consume_task_or_control_messages():
    redis = AsyncMock()
    transport = _transport(redis)
    is_current = _enable_fencing(transport, current=False)

    with pytest.raises(RuntimeError, match="generation"):
        await transport.poll_task(timeout=0.1)
    with pytest.raises(RuntimeError, match="generation"):
        await transport.poll_control(timeout=0.1)

    assert is_current.await_count == 2
    redis.xreadgroup.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_generation_cannot_submit_task_or_control_results():
    redis = AsyncMock()
    transport = _transport(redis)
    _enable_fencing(transport, current=False)

    task_result = SimpleNamespace()
    assert await transport.report_result(task_result) is False
    assert (
        await transport.send_control_result(
            "worker-1:" + "a" * 32,
            "antcode:control:reply:worker-1:" + "a" * 32,
            True,
            receipt="antcode:control:worker-1|1-0",
        )
        is False
    )

    redis.xadd.assert_not_awaited()
    redis.xrange.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_result_embeds_lease_and_reports_generation_loss_after_xadd():
    redis = AsyncMock()
    redis.xadd.return_value = "1-0"
    transport = _transport(redis)
    transport._lease_id = "lease-1"
    is_current = AsyncMock(side_effect=[True, False])
    transport._lease_store = SimpleNamespace(is_current=is_current)
    transport._lease_fencing_enabled = True
    result = TaskResult(run_id="run-1", task_id="task-1", status="success", data={})

    assert await transport.report_result(result) is False

    fields = redis.xadd.await_args.args[1]
    status = data_pb2.TaskStatus.FromString(fields[PROTO_FIELD])
    assert status.data["lease_id"] == "lease-1"
    assert result.data == {}


@pytest.mark.asyncio
async def test_stale_generation_deregister_does_not_delete_current_heartbeat():
    redis = AsyncMock()
    transport = _transport(redis)
    transport._lease_id = "stale-lease"
    transport._lease_store = SimpleNamespace(revoke=AsyncMock(return_value=False))

    await transport.deregister("shutdown")

    transport._lease_store.revoke.assert_awaited_once_with(
        "worker-1",
        lease_id="stale-lease",
        reason="shutdown",
    )
    redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_ack_control_rejects_forged_stream_before_redis_access():
    redis = AsyncMock()
    transport = _transport(redis)

    assert await transport.ack_control("antcode:control:worker-2|1-0") is False
    redis.xpending_range.assert_not_awaited()
    redis.xack.assert_not_awaited()


@pytest.mark.asyncio
async def test_ack_control_rejects_foreign_pel_owner_and_rewinds_recovery():
    redis = AsyncMock()
    redis.eval.return_value = -1
    transport = _transport(redis)
    transport._control_recovery._channel_index = len(transport._control_channels)

    assert await transport.ack_control("antcode:control:worker-1|1-0") is False

    assert transport._control_recovery.complete is False
    redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_ack_control_failure_rewinds_pel_for_same_process_retry(monkeypatch):
    redis = AsyncMock()
    redis.eval.side_effect = RuntimeError("redis unavailable")
    transport = _transport(redis)
    transport._control_recovery._channel_index = len(transport._control_channels)

    assert await transport.ack_control("antcode:control:worker-1|1-0") is False
    assert transport._control_recovery.complete is False


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_direct_transport_rejects_non_standard_json_constants(constant: str):
    transport = RedisTransport(redis_url="redis://localhost/0", worker_id="worker-1")

    with pytest.raises(ValueError, match="合法 JSON"):
        transport._decode_data({"payload": f'{{"value":{constant}}}'})


def test_json_codec_rejects_non_finite_nested_values_on_both_boundaries():
    codec = JsonCodec()

    with pytest.raises(CodecError):
        codec.encode({"payload": {"value": float("nan")}})
    with pytest.raises(CodecError):
        codec.decode({"payload": '{"value":NaN}', "_schema_version": "v1"}, dict)


def test_settlement_evidence_ttl_covers_maximum_runtime_action():
    ttl = settlement_ttl_seconds({}, now_ms=0)

    assert ttl == MAX_RUNTIME_ACTION_SECONDS + SETTLEMENT_RECOVERY_GRACE_SECONDS
    assert ttl > CONTROL_REPLY_TTL_SECONDS


def test_settlement_evidence_ttl_uses_redis_timeline():
    ttl = settlement_ttl_seconds({"expires_at_ms": 2_000_001}, now_ms=1_000_000)

    assert ttl == 1_001 + SETTLEMENT_RECOVERY_GRACE_SECONDS


def test_runtime_settlement_fingerprint_is_lease_generation_bound():
    source = ControlSource(ControlChannel("stream", "group"), "1-0", "consumer")
    result = RuntimeControlResult("request", "reply", True, None, "")
    common = {"source": source, "result": result, "data_json": "null", "worker_id": "worker-1"}

    first = settlement_fingerprint(**common, lease_id="lease-1")
    second = settlement_fingerprint(**common, lease_id="lease-2")

    assert first != second
