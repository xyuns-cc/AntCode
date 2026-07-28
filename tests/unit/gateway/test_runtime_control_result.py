from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import control_pb2
from antcode_gateway.services import control_service as control_service_module
from antcode_gateway.services.control_service import GatewayControlService
from antcode_gateway.services.runtime_control_settlement_store import settlement_key
from redis.exceptions import ResponseError


class _Pipeline:
    def __init__(self, redis: RuntimeResultRedis) -> None:
        self.redis = redis
        self.commands: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def xadd(self, stream, payload, **kwargs):
        self.commands.append(("xadd", stream, payload, kwargs))

    def expire(self, stream, ttl):
        self.commands.append(("expire", stream, ttl))

    async def execute(self):
        name, stream, payload, kwargs = self.commands[0]
        assert name == "xadd"
        message_id = kwargs["id"]
        if (stream, message_id) in self.redis.entries:
            raise ResponseError("equal or smaller stream ID")
        self.redis.entries[(stream, message_id)] = dict(payload)
        self.redis.operations.extend(["xadd", "expire"])
        return [message_id, True]


class RuntimeResultRedis:
    def __init__(
        self,
        *,
        request_id: str = "request-1",
        control_type: str = "runtime_manage",
        source: str = "antcode:control:worker-1",
    ) -> None:
        self.operations: list[str] = []
        self.ack_requests: list[tuple[str, str, str]] = []
        self.pending_groups: list[str] = []
        self.values: dict[str, str] = {}
        self.pending_owner = "worker-1"
        self.current_lease_id = "lease-1"
        self.ack_count = 1
        self.entries: dict[tuple[str, str], dict] = {
            (source, "1-0"): {
                "control_type": control_type,
                "request_id": request_id,
                "reply_stream": f"antcode:control:reply:{request_id}",
            }
        }

    async def get(self, key):
        self.operations.append("get")
        return self.values.get(key)

    async def eval(self, *_args):
        _, _, _, marker_key, lease_id, marker, fingerprint, _ = _args
        self.operations.append("set")
        if self.current_lease_id != lease_id:
            return [-2, ""]
        stored = self.values.get(marker_key)
        if stored is not None and stored not in {marker, fingerprint}:
            return [-1, stored]
        self.values[marker_key] = stored or marker
        return [0 if stored else 1, self.values[marker_key]]

    async def xrange(self, stream, *, min, max, count):
        self.operations.append("xrange")
        payload = self.entries.get((stream, min))
        return [] if payload is None else [(min, payload)]

    async def xpending_range(self, stream, group, *, min, max, count, consumername=None):
        self.operations.append("xpending")
        self.pending_groups.append(group)
        if self.pending_owner is None:
            return []
        if consumername is not None and self.pending_owner != consumername:
            return []
        return [{"message_id": min, "consumer": self.pending_owner}]

    def pipeline(self, *, transaction):
        assert transaction is True
        return _Pipeline(self)

    async def expire(self, stream, ttl):
        self.operations.append("expire")
        return True

    async def xack(self, stream, group, message_id):
        self.operations.append("xack")
        self.ack_requests.append((stream, group, message_id))
        return self.ack_count


def _context() -> MagicMock:
    context = MagicMock()
    context.abort = AsyncMock()
    return context


def _request(**overrides) -> control_pb2.AckControlRequest:
    values = {
        "worker_id": "worker-1",
        "event_id": "antcode:control:worker-1|1-0",
        "success": True,
        "request_id": "request-1",
        "data_json": '{"中文":[true,null],"envs":["shared-py312"]}',
        "lease_id": "lease-1",
    }
    values.update(overrides)
    return control_pb2.AckControlRequest(**values)


def _install(monkeypatch, redis: RuntimeResultRedis) -> None:
    monkeypatch.setattr(control_service_module, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(control_service_module, "require_authenticated_worker", AsyncMock(return_value="worker-1"))
    monkeypatch.setattr(control_service_module, "trim_acknowledged_stream", AsyncMock())


def _service() -> GatewayControlService:
    lease_store = MagicMock()
    lease_store.is_current = AsyncMock(return_value=True)
    return GatewayControlService(lease_store=lease_store)


@pytest.mark.asyncio
async def test_runtime_result_evidence_is_persisted_before_reply_and_xack(monkeypatch):
    redis = RuntimeResultRedis()
    _install(monkeypatch, redis)
    response = await _service().AckControl(_request(), _context())

    assert response.received is True
    assert redis.operations.index("set") < redis.operations.index("xadd") < redis.operations.index("xack")
    reply = redis.entries[("antcode:control:reply:request-1", "1-0")]
    assert reply["data"] == '{"envs":["shared-py312"],"中文":[true,null]}'
    assert reply["request_id"] == "request-1"
    assert len(reply["settlement"]) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ack_request", "request_id"),
    [
        (_request(request_id="forged-request"), "expected-request"),
        (_request(request_id=""), "request-1"),
        (_request(data_json=""), "request-1"),
        (_request(data_json="NaN"), "request-1"),
    ],
)
async def test_invalid_runtime_result_is_not_acked(monkeypatch, ack_request, request_id):
    redis = RuntimeResultRedis(request_id=request_id)
    _install(monkeypatch, redis)

    response = await _service().AckControl(ack_request, _context())

    assert response.received is False
    assert "xack" not in redis.operations


@pytest.mark.asyncio
async def test_plain_control_ack_only_xacks(monkeypatch):
    redis = RuntimeResultRedis(control_type="cancel")
    _install(monkeypatch, redis)
    request = _request(request_id="", data_json="")
    response = await _service().AckControl(request, _context())

    assert response.received is True
    assert "xadd" not in redis.operations
    assert redis.operations[-1] == "xack"


@pytest.mark.asyncio
async def test_global_control_ack_uses_worker_group_and_all_group_trim(monkeypatch):
    source = "antcode:control:global"
    redis = RuntimeResultRedis(control_type="cancel", source=source)
    _install(monkeypatch, redis)
    request = _request(
        event_id=f"{source}|1-0",
        request_id="",
        data_json="",
    )

    response = await _service().AckControl(request, _context())

    assert response.received is True
    assert redis.pending_groups == ["antcode-control:worker-1"]
    assert redis.ack_requests == [(source, "antcode-control:worker-1", "1-0")]
    control_service_module.trim_acknowledged_stream.assert_awaited_once_with(redis, source, None)


@pytest.mark.asyncio
async def test_global_runtime_control_cannot_be_settled_directly(monkeypatch):
    source = "antcode:control:global"
    redis = RuntimeResultRedis(source=source)
    _install(monkeypatch, redis)
    request = _request(event_id=f"{source}|1-0")

    response = await _service().AckControl(request, _context())

    assert response.received is False
    assert "xadd" not in redis.operations
    assert "set" not in redis.operations
    assert "xack" not in redis.operations


@pytest.mark.asyncio
async def test_plain_control_rejects_runtime_fields(monkeypatch):
    redis = RuntimeResultRedis(control_type="config_update")
    _install(monkeypatch, redis)
    response = await _service().AckControl(_request(), _context())

    assert response.received is False
    assert "xack" not in redis.operations


@pytest.mark.asyncio
async def test_control_ack_requires_authenticated_pending_owner(monkeypatch):
    redis = RuntimeResultRedis()
    redis.pending_owner = "worker-2"
    _install(monkeypatch, redis)
    response = await _service().AckControl(_request(), _context())

    assert response.received is False
    assert "xadd" not in redis.operations
    assert "xack" not in redis.operations


@pytest.mark.asyncio
async def test_identical_committed_retry_is_idempotent(monkeypatch):
    redis = RuntimeResultRedis()
    _install(monkeypatch, redis)
    service = _service()
    request = _request()
    first = await service.AckControl(request, _context())
    redis.ack_count = 0
    writes = redis.operations.count("xadd")

    second = await service.AckControl(request, _context())

    assert first.received is True
    assert second.received is True
    assert redis.operations.count("xadd") == writes


@pytest.mark.asyncio
async def test_conflicting_committed_retry_is_rejected(monkeypatch):
    redis = RuntimeResultRedis()
    _install(monkeypatch, redis)
    service = _service()
    await service.AckControl(_request(), _context())
    ack_count = redis.operations.count("xack")

    response = await service.AckControl(_request(data_json='{"different":true}'), _context())

    assert response.received is False
    assert redis.operations.count("xack") == ack_count


@pytest.mark.asyncio
async def test_reply_written_before_marker_is_recovered_without_duplicate(monkeypatch):
    redis = RuntimeResultRedis()
    _install(monkeypatch, redis)
    service = _service()
    request = _request()
    await service.AckControl(request, _context())
    redis.values.pop(settlement_key(request.event_id))
    redis.operations.clear()

    response = await service.AckControl(request, _context())
    assert response.received is True
    assert "xadd" not in redis.operations
    assert "set" in redis.operations
    assert "xack" in redis.operations


@pytest.mark.asyncio
async def test_duplicate_plain_ack_after_trim_is_idempotent_without_abort(monkeypatch):
    """G1 回归：结算后 stream entry 被裁剪，重复 ACK 幂等返回 received=False，
    不得映射为 gRPC INVALID_ARGUMENT（否则 at-least-once 重试被记为连接错误）。"""
    redis = RuntimeResultRedis(control_type="cancel")
    _install(monkeypatch, redis)
    service = _service()
    request = _request(request_id="", data_json="")
    first = await service.AckControl(request, _context())

    # 模拟结算完成后的状态：entry 已裁剪、PEL 已清空
    redis.entries.clear()
    redis.pending_owner = None
    redis.ack_count = 0
    retry_context = _context()
    second = await service.AckControl(request, retry_context)

    assert first.received is True
    assert second.received is False
    retry_context.abort.assert_not_awaited()


@pytest.mark.asyncio
async def test_ack_gone_from_pel_but_entry_present_is_idempotent_without_abort(monkeypatch):
    """G1 回归：entry 尚在 stream 但已不在本 Worker PEL（已 ACK 未裁剪）时，
    同样按良性重放处理。"""
    redis = RuntimeResultRedis(control_type="cancel")
    redis.pending_owner = None
    _install(monkeypatch, redis)
    context = _context()

    response = await _service().AckControl(_request(request_id="", data_json=""), context)

    assert response.received is False
    context.abort.assert_not_awaited()
    assert "xack" not in redis.operations


@pytest.mark.asyncio
async def test_foreign_pending_owner_still_aborts_invalid_argument(monkeypatch):
    """G1 边界：PEL 里 entry 归属其他 Worker 属于真实归属违规，保持硬错误。"""
    redis = RuntimeResultRedis(control_type="cancel")
    redis.pending_owner = "worker-2"
    _install(monkeypatch, redis)
    context = _context()

    response = await _service().AckControl(_request(request_id="", data_json=""), context)

    assert response.received is False
    context.abort.assert_awaited_once()
    assert context.abort.await_args.args[0] is grpc.StatusCode.INVALID_ARGUMENT
    assert "xack" not in redis.operations


@pytest.mark.asyncio
async def test_committed_marker_repairs_a_missing_reply(monkeypatch):
    redis = RuntimeResultRedis()
    _install(monkeypatch, redis)
    service = _service()
    request = _request()
    await service.AckControl(request, _context())
    redis.entries.pop(("antcode:control:reply:request-1", "1-0"))
    redis.ack_count = 0
    redis.operations.clear()

    response = await service.AckControl(request, _context())
    assert response.received is True
    assert "xadd" in redis.operations
    assert redis.entries[("antcode:control:reply:request-1", "1-0")]["request_id"] == "request-1"
