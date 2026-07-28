"""RedisTransport 现代化行为测试。"""

import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import data_pb2
from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD
from antcode_worker.transport.base import HeartbeatMessage, LogMessage
from antcode_worker.transport.redis.transport import RedisTransport
from redis.cluster import key_slot
from redis.exceptions import ConnectionError as RedisConnectionError

redis_transport_module = sys.modules[RedisTransport.__module__]
_RUNTIME_REQUEST_ID = f"worker-1:{'a' * 32}"
_RUNTIME_REPLY_STREAM = f"antcode:control:reply:{_RUNTIME_REQUEST_ID}"
_FOREIGN_RUNTIME_REQUEST_ID = f"worker-2:{'b' * 32}"


class _FakePipeline:
    def __init__(self, execute_result=None):
        self.execute_result = execute_result if execute_result is not None else [b"1-0"]
        self.commands = []

    def xadd(self, *args, **kwargs):
        self.commands.append(("xadd", args, kwargs))
        return self

    def expire(self, *args, **kwargs):
        self.commands.append(("expire", args, kwargs))
        return self

    def pexpireat(self, *args, **kwargs):
        self.commands.append(("pexpireat", args, kwargs))
        return self

    def hset(self, *args, **kwargs):
        self.commands.append(("hset", args, kwargs))
        return self

    async def execute(self, **kwargs):
        self.commands.append(("execute", (), kwargs))
        return self.execute_result


class _FailingPipeline(_FakePipeline):
    async def execute(self, **kwargs):
        self.commands.append(("execute", (), kwargs))
        raise RuntimeError("ID specified in XADD is equal or smaller")


class _RuntimePipeline(_FakePipeline):
    def __init__(self, redis):
        super().__init__(["2-0", True])
        self.redis = redis

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, **kwargs):
        if self.redis.fail_next_reply_write:
            self.redis.fail_next_reply_write = False
            raise RedisConnectionError("reply write failed before apply")
        xadd = next(command for command in self.commands if command[0] == "xadd")
        stream, fields = xadd[1]
        self.redis.replies[stream] = (xadd[2]["id"], dict(fields))
        expiry = next(command for command in self.commands if command[0] == "pexpireat")
        self.redis.expiries[expiry[1][0]] = expiry[1][1]
        return await super().execute(**kwargs)


class _RuntimeControlRedis:
    def __init__(self, *, stream="antcode:control:worker-1", source_fields=None):
        self.stream = stream
        self.message_id = "2-0"
        self.source_fields = source_fields or {
            "control_type": "runtime_manage",
            "request_id": _RUNTIME_REQUEST_ID,
            "reply_stream": _RUNTIME_REPLY_STREAM,
            "payload": "{}",
        }
        self.replies = {}
        self.values = {}
        self.expiries = {}
        self.pending = True
        self.lose_first_ack = False
        self.fail_next_ack_before_apply = False
        self.fail_next_marker_write = False
        self.fail_next_reply_write = False
        self.current_lease_id = "lease-1"
        self.xpending_calls = 0
        self.xack_calls = 0
        self.pending_requests = []
        self.ack_requests = []
        self.pipelines = []

    async def xrange(self, stream, **kwargs):
        if stream == self.stream:
            return [(self.message_id, self.source_fields)]
        entry = self.replies.get(stream)
        return [entry] if entry else []

    async def xpending_range(self, stream, group, **kwargs):
        self.xpending_calls += 1
        self.pending_requests.append((stream, group, kwargs))
        if not self.pending:
            return []
        return [{"message_id": self.message_id, "consumer": "worker-1"}]

    def pipeline(self, **kwargs):
        pipeline = _RuntimePipeline(self)
        self.pipelines.append(pipeline)
        return pipeline

    async def set(self, key, value, **kwargs):
        if kwargs.get("nx") and key in self.values:
            return False
        self.values[key] = value
        if "pxat" in kwargs:
            self.expiries[key] = kwargs["pxat"]
        return True

    async def eval(self, *args):
        if len(args) == 6:
            _script, _numkeys, stream, group, message_id, _consumer = args
            return await self.xack(stream, group, message_id)
        _script, _numkeys, marker_key, _lease_key, expected_lease_id, encoded, expires_at_ms = args
        if self.fail_next_marker_write:
            self.fail_next_marker_write = False
            raise RedisConnectionError("marker write failed before apply")
        if expected_lease_id != self.current_lease_id:
            return -2
        existing = self.values.get(marker_key)
        if existing is not None and existing != encoded:
            return -1
        self.values[marker_key] = encoded
        self.expiries[marker_key] = int(expires_at_ms)
        return int(existing is None)

    async def get(self, key):
        return self.values.get(key)

    async def expire(self, key, ttl):
        return key in self.replies or key in self.values

    async def pexpireat(self, key, expires_at_ms):
        if key not in self.replies and key not in self.values:
            return False
        self.expiries[key] = expires_at_ms
        return True

    async def time(self):
        return (1_000, 500_000)

    async def xack(self, stream, group, message_id):
        self.xack_calls += 1
        self.ack_requests.append((stream, group, message_id))
        if self.fail_next_ack_before_apply:
            self.fail_next_ack_before_apply = False
            raise RedisConnectionError("ACK failed before apply")
        if self.lose_first_ack:
            self.lose_first_ack = False
            self.pending = False
            raise RedisConnectionError("ACK response lost")
        if not self.pending:
            return 0
        self.pending = False
        return 1


class _RuntimeLeaseStore:
    def __init__(self, current_lease_id):
        self.current_lease_id = current_lease_id
        self.calls = []

    async def is_current(self, worker_id, lease_id):
        self.calls.append((worker_id, lease_id))
        return lease_id == self.current_lease_id

    def lease_key(self, worker_id):
        return f"{{antcode}}:lease:data:{worker_id}"


def _runtime_transport(redis, *, namespace="antcode", lease_store=None):
    transport = RedisTransport(
        redis_url="redis://localhost:6379/0",
        worker_id="worker-1",
        namespace=namespace,
    )
    transport._running = True
    transport._redis = redis
    transport._lease_id = "lease-1"
    transport._lease_store = lease_store or _RuntimeLeaseStore("lease-1")
    transport._lease_fencing_enabled = True
    return transport


def _source_fields() -> dict[str, str]:
    digest = "a" * 64
    return {
        "source_bundle_uri": f"pgartifact://{digest}",
        "source_bundle_sha256": digest,
        "source_bundle_size": "1",
    }


@pytest.mark.asyncio
async def test_poll_task_exposes_redis_errors():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = RuntimeError("redis unavailable")
    transport._redis = fake_redis

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await transport.poll_task(timeout=0.1)


@pytest.mark.asyncio
async def test_poll_control_exposes_redis_errors():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = RuntimeError("redis unavailable")
    transport._redis = fake_redis

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await transport.poll_control(timeout=0.1)


@pytest.mark.asyncio
async def test_poll_control_drains_own_consumer_pel_before_new_events():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    worker_stream = "antcode:control:worker-1"
    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [
        [(worker_stream, [("1-0", {"control_type": "cancel"}), ("2-0", {"control_type": "kill"})])],
        [],
        [(worker_stream, [("3-0", {"control_type": "ping"})])],
    ]
    transport._redis = fake_redis

    expected_receipts = [
        f"{worker_stream}|1-0",
        f"{worker_stream}|2-0",
        f"{worker_stream}|3-0",
    ]
    receipts = []
    for _expected in expected_receipts:
        message = await transport.poll_control(timeout=0.1)
        assert message is not None
        receipts.append(message.receipt)

    assert receipts == expected_receipts
    calls = fake_redis.xreadgroup.await_args_list
    assert [call.kwargs["streams"] for call in calls] == [
        {worker_stream: "0-0"},
        {worker_stream: "2-0"},
        {worker_stream: ">"},
    ]
    first_pending, second_pending, fresh = calls
    assert "block" not in first_pending.kwargs
    assert "block" not in second_pending.kwargs
    assert fresh.kwargs["block"] > 0


def test_direct_control_channels_exclude_shared_global_stream():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")

    assert [(channel.stream_key, channel.group) for channel in transport._control_channels] == [
        ("antcode:control:worker-1", "antcode-control")
    ]


@pytest.mark.asyncio
async def test_send_control_result_recovers_lost_ack_without_reexecution(monkeypatch):
    redis = _RuntimeControlRedis()
    redis.lose_first_ack = True
    transport = _runtime_transport(redis)
    monkeypatch.setattr(redis_transport_module, "trim_acknowledged_stream", AsyncMock())
    kwargs = {
        "request_id": _RUNTIME_REQUEST_ID,
        "reply_stream": _RUNTIME_REPLY_STREAM,
        "success": True,
        "receipt": "antcode:control:worker-1|2-0",
        "data": {"value": 1},
    }

    assert await transport.send_control_result(**kwargs) is False
    assert await transport.send_control_result(**kwargs) is True

    assert len(redis.pipelines) == 1
    assert redis.xpending_calls == 1
    assert redis.xack_calls == 2
    reply = redis.replies[_RUNTIME_REPLY_STREAM][1]
    assert reply["data"] == '{"value": 1}'
    assert len(redis.values) == 1
    marker_key = next(iter(redis.values))
    assert marker_key.startswith("{antcode}:control:settlement:worker-1:")
    assert redis.expiries[marker_key] == redis.expiries[_RUNTIME_REPLY_STREAM]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["before_xack", "after_marker", "reply_evicted"])
async def test_new_generation_recovers_commit_before_xack_with_fencing(monkeypatch, failure_mode):
    redis = _RuntimeControlRedis()
    redis.fail_next_reply_write = failure_mode == "after_marker"
    redis.fail_next_ack_before_apply = failure_mode != "after_marker"
    lease_store = _RuntimeLeaseStore("lease-1")
    old_transport = _runtime_transport(redis, lease_store=lease_store)
    monkeypatch.setattr(redis_transport_module, "trim_acknowledged_stream", AsyncMock())

    committed = await old_transport.send_control_result(
        _RUNTIME_REQUEST_ID,
        _RUNTIME_REPLY_STREAM,
        True,
        receipt="antcode:control:worker-1|2-0",
        data={"value": 1},
    )
    assert committed is False
    assert len(redis.pipelines) == 1
    assert redis.pending is True
    assert len(redis.values) == 1
    if failure_mode == "reply_evicted":
        redis.replies.clear()

    lease_store.current_lease_id = "lease-2"
    redis.current_lease_id = "lease-2"
    with pytest.raises(RuntimeError, match="generation"):
        await old_transport._require_current_generation()
    new_transport = _runtime_transport(redis, lease_store=lease_store)
    new_transport._lease_id = "lease-2"
    message = await new_transport._decode_control_delivery(
        "antcode:control:worker-1",
        "2-0",
        redis.source_fields,
    )

    assert message is None
    assert len(redis.pipelines) == 1 + int(failure_mode in {"after_marker", "reply_evicted"})
    assert redis.xack_calls == 1 + int(failure_mode != "after_marker")
    assert redis.pending is False
    assert len(redis.values) == 1
    assert redis.replies[_RUNTIME_REPLY_STREAM][1]["lease_id"] == "lease-1"
    assert ("worker-1", "lease-1") in lease_store.calls
    assert ("worker-1", "lease-2") in lease_store.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["marker", "reply"])
async def test_runtime_recovery_quarantines_corrupt_evidence_without_head_of_line_blocking(monkeypatch, corruption):
    redis = _RuntimeControlRedis()
    redis.fail_next_ack_before_apply = True
    transport = _runtime_transport(redis)
    trim = AsyncMock()
    monkeypatch.setattr(redis_transport_module, "trim_acknowledged_stream", trim)

    committed = await transport.send_control_result(
        _RUNTIME_REQUEST_ID,
        _RUNTIME_REPLY_STREAM,
        True,
        receipt="antcode:control:worker-1|2-0",
        data={"value": 1},
    )
    assert committed is False
    if corruption == "marker":
        marker_key = next(iter(redis.values))
        redis.values[marker_key] = "not-json"
    else:
        redis.replies[_RUNTIME_REPLY_STREAM][1]["data"] = '{"value":2}'

    recovered = await transport._decode_control_delivery(
        "antcode:control:worker-1",
        "2-0",
        redis.source_fields,
    )

    assert recovered is None
    assert redis.pending is False
    assert redis.xack_calls == 2
    trim.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_marker_commit_rechecks_lease_inside_redis_lua():
    redis = _RuntimeControlRedis()
    transport = _runtime_transport(redis)
    redis.current_lease_id = "lease-2"

    committed = await transport.send_control_result(
        _RUNTIME_REQUEST_ID,
        _RUNTIME_REPLY_STREAM,
        True,
        receipt="antcode:control:worker-1|2-0",
        data={"value": 1},
    )

    assert committed is False
    assert redis.values == {}
    assert redis.replies == {}
    assert redis.xack_calls == 0


@pytest.mark.asyncio
async def test_send_control_result_keeps_first_committed_result_on_conflicting_retry(monkeypatch):
    redis = _RuntimeControlRedis()
    transport = _runtime_transport(redis)
    monkeypatch.setattr(redis_transport_module, "trim_acknowledged_stream", AsyncMock())
    common = {
        "request_id": _RUNTIME_REQUEST_ID,
        "reply_stream": _RUNTIME_REPLY_STREAM,
        "success": True,
        "receipt": "antcode:control:worker-1|2-0",
    }

    assert await transport.send_control_result(**common, data={"value": 1}) is True
    assert await transport.send_control_result(**common, data={"value": 2}) is True

    assert len(redis.pipelines) == 1
    assert redis.xack_calls == 2
    assert redis.replies[_RUNTIME_REPLY_STREAM][1]["data"] == '{"value": 1}'


@pytest.mark.asyncio
async def test_send_control_result_requires_pending_owner_before_first_commit():
    redis = _RuntimeControlRedis()
    redis.pending = False
    transport = _runtime_transport(redis)

    success = await transport.send_control_result(
        _RUNTIME_REQUEST_ID,
        _RUNTIME_REPLY_STREAM,
        True,
        receipt="antcode:control:worker-1|2-0",
        data=None,
    )

    assert success is False
    assert redis.pipelines == []
    assert redis.xack_calls == 0


@pytest.mark.asyncio
async def test_send_control_result_rejects_foreign_worker_request_scope_before_redis_writes():
    foreign_reply = f"antcode:control:reply:{_FOREIGN_RUNTIME_REQUEST_ID}"
    redis = _RuntimeControlRedis(
        source_fields={
            "control_type": "runtime_manage",
            "request_id": _FOREIGN_RUNTIME_REQUEST_ID,
            "reply_stream": foreign_reply,
        }
    )
    transport = _runtime_transport(redis)

    success = await transport.send_control_result(
        _FOREIGN_RUNTIME_REQUEST_ID,
        foreign_reply,
        True,
        receipt="antcode:control:worker-1|2-0",
        data=None,
    )

    assert success is False
    assert redis.pipelines == []
    assert redis.xpending_calls == 0
    assert redis.xack_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_fields", "reply_stream", "receipt"),
    [
        (
            {"control_type": "cancel", "request_id": _RUNTIME_REQUEST_ID, "reply_stream": _RUNTIME_REPLY_STREAM},
            _RUNTIME_REPLY_STREAM,
            "antcode:control:worker-1|2-0",
        ),
        (
            {
                "control_type": "runtime_manage",
                "request_id": _FOREIGN_RUNTIME_REQUEST_ID,
                "reply_stream": _RUNTIME_REPLY_STREAM,
            },
            _RUNTIME_REPLY_STREAM,
            "antcode:control:worker-1|2-0",
        ),
        (
            {
                "control_type": "runtime_manage",
                "request_id": _RUNTIME_REQUEST_ID,
                "reply_stream": "antcode:control:reply:forged",
            },
            _RUNTIME_REPLY_STREAM,
            "antcode:control:worker-1|2-0",
        ),
        (
            {
                "control_type": "runtime_manage",
                "request_id": _RUNTIME_REQUEST_ID,
                "reply_stream": _RUNTIME_REPLY_STREAM,
            },
            _RUNTIME_REPLY_STREAM,
            "antcode:task:ready:worker-1|2-0",
        ),
    ],
)
async def test_send_control_result_rejects_forged_source(source_fields, reply_stream, receipt):
    redis = _RuntimeControlRedis(source_fields=source_fields)
    transport = _runtime_transport(redis)

    success = await transport.send_control_result(
        _RUNTIME_REQUEST_ID,
        reply_stream,
        True,
        receipt=receipt,
        data=None,
    )

    assert success is False
    assert redis.pipelines == []
    assert redis.xack_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "error"),
    [
        (float("nan"), ""),
        ("x" * (1024 * 1024), ""),
        (None, "x" * (16 * 1024 + 1)),
    ],
)
async def test_send_control_result_enforces_strict_json_and_size_limits(data, error):
    redis = _RuntimeControlRedis()
    transport = _runtime_transport(redis)

    success = await transport.send_control_result(
        _RUNTIME_REQUEST_ID,
        _RUNTIME_REPLY_STREAM,
        True,
        receipt="antcode:control:worker-1|2-0",
        data=data,
        error=error,
    )

    assert success is False
    assert redis.pipelines == []
    assert redis.xpending_calls == 0


@pytest.mark.asyncio
async def test_send_control_result_rejects_global_runtime_receipt(monkeypatch):
    redis = _RuntimeControlRedis(stream="antcode:control:global")
    transport = _runtime_transport(redis)
    monkeypatch.setattr(redis_transport_module, "trim_acknowledged_stream", AsyncMock())

    success = await transport.send_control_result(
        _RUNTIME_REQUEST_ID,
        _RUNTIME_REPLY_STREAM,
        True,
        receipt="antcode:control:global|2-0",
        data=None,
    )

    assert success is False
    assert redis.xpending_calls == 0
    assert redis.xack_calls == 0
    assert redis.pipelines == []


@pytest.mark.asyncio
async def test_poll_task_only_accepts_run_id_and_ignores_legacy_execution_id():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.return_value = [
        (
            "antcode:task:ready:worker-1",
            [
                (
                    "1-0",
                    {
                        "task_id": "task-1",
                        "project_id": "proj-1",
                        "execution_id": "legacy-run",
                        **_source_fields(),
                    },
                )
            ],
        )
    ]
    transport._redis = fake_redis

    task = await transport.poll_task(timeout=0.1)

    assert task is not None
    assert task.task_id == "task-1"
    assert task.run_id == ""


@pytest.mark.asyncio
async def test_poll_task_dead_letters_legacy_project_path_frame():
    # P1-DR-02: 坏帧（含废弃 project_path）不能再反复重投堵塞当前代际
    # PEL —— 当前 consumer 直接 DLQ + ACK，poll 返回 None 继续处理后续任务。
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    transport._require_current_generation = AsyncMock()

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.return_value = [
        (
            "antcode:task:ready:worker-1",
            [
                (
                    "1-0",
                    {
                        "task_id": "task-1",
                        "project_id": "proj-1",
                        "project_path": "/tmp/legacy",
                        "source_bundle_uri": "pgartifact://" + "a" * 64,
                        "source_bundle_sha256": "a" * 64,
                        "source_bundle_size": "1",
                    },
                )
            ],
        )
    ]
    transport._redis = fake_redis
    transport._reclaimer = MagicMock(dead_letter_owned=AsyncMock())

    assert await transport.poll_task(timeout=0.1) is None

    call = transport._reclaimer.dead_letter_owned.await_args
    assert call.args[0] == "antcode:task:ready:worker-1"
    assert call.args[1] == "1-0"
    payload = call.args[2]
    assert payload["project_path"] == "/tmp/legacy"
    assert "project_path" in payload["_bad_frame_error"]


@pytest.mark.asyncio
async def test_poll_control_only_accepts_run_id_and_ignores_legacy_execution_id():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.return_value = [
        (
            "antcode:control:worker-1",
            [("2-0", {"control_type": "cancel", "task_id": "task-1", "execution_id": "legacy-run"})],
        )
    ]
    transport._redis = fake_redis

    control = await transport.poll_control(timeout=0.1)

    assert control is not None
    assert control.control_type == "cancel"
    assert control.task_id == "task-1"
    assert control.run_id == ""


@pytest.mark.asyncio
async def test_ack_task_returns_false_when_xack_acknowledges_no_messages():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True

    fake_redis = AsyncMock()
    fake_redis.eval.return_value = 0
    transport._redis = fake_redis

    receipt = "antcode:task:ready:worker-1|1-0"

    assert await transport.ack_task(receipt, accepted=True) is False
    assert receipt not in transport._receipt_cache


@pytest.mark.asyncio
async def test_ack_task_atomically_requires_owned_worker_receipt():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    fake_redis = AsyncMock()
    fake_redis.eval.return_value = 1
    transport._redis = fake_redis
    receipt = "antcode:task:ready:worker-1|1-0"
    transport._receipt_cache[receipt] = ("antcode:task:ready:worker-1", "1-0", {})

    assert await transport.ack_task(receipt, accepted=True) is True

    args = fake_redis.eval.await_args.args
    assert args[1] == 2
    assert args[2] == "antcode:task:ready:worker-1"
    assert ":ack:" in args[3]
    assert key_slot(args[2].encode()) == key_slot(args[3].encode())
    assert args[6] == transport._task_consumer_name


@pytest.mark.asyncio
async def test_ack_task_rejects_foreign_stream_even_if_receipt_is_cached():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    fake_redis = AsyncMock()
    transport._redis = fake_redis
    receipt = "antcode:task:ready:worker-2|1-0"
    transport._receipt_cache[receipt] = ("antcode:task:ready:worker-2", "1-0", {})

    assert await transport.ack_task(receipt, accepted=True) is False
    fake_redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_requeue_task_uses_atomic_same_slot_idempotent_settlement():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    fake_redis = AsyncMock()
    fake_redis.time.return_value = (1_000, 0)
    fake_redis.eval.return_value = [1, "2-0"]
    transport._redis = fake_redis
    receipt = "antcode:task:ready:worker-1|1-0"
    transport._receipt_cache[receipt] = ("antcode:task:ready:worker-1", "1-0", {"task_id": "task-1"})

    assert await transport.requeue_task(receipt, reason="busy") is True

    args = fake_redis.eval.await_args.args
    assert args[1] == 2
    assert args[2] == "antcode:task:ready:worker-1"
    assert ":requeue:" in args[3]
    assert key_slot(args[2].encode()) == key_slot(args[3].encode())
    assert receipt not in transport._receipt_cache


@pytest.mark.asyncio
async def test_requeue_task_recovers_lost_lua_response_without_duplicate(monkeypatch):
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    fake_redis = AsyncMock()
    fake_redis.time.return_value = (1_000, 0)
    fake_redis.eval.side_effect = [RedisConnectionError("response lost"), [0, "2-0"]]
    transport._redis = fake_redis
    monkeypatch.setattr(transport, "reconnect", AsyncMock(return_value=True))
    receipt = "antcode:task:ready:worker-1|1-0"
    transport._receipt_cache[receipt] = ("antcode:task:ready:worker-1", "1-0", {"task_id": "task-1"})

    assert await transport.requeue_task(receipt, reason="busy") is True
    assert fake_redis.eval.await_count == 2


@pytest.mark.asyncio
async def test_requeue_task_treats_non_numeric_requeue_count_as_zero():
    """U6/X1 回归：被污染的非数字 requeue_count 按 0 处理。

    退化行为是 int() 抛 ValueError → 外层 except 返回 False，毒消息
    永远卡在 PEL 里等 reclaimer 兜底，requeue/DLQ 通路直接断掉。
    """
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    fake_redis = AsyncMock()
    fake_redis.time.return_value = (1_000, 0)
    fake_redis.eval.return_value = [1, "2-0"]
    transport._redis = fake_redis
    receipt = "antcode:task:ready:worker-1|1-0"
    transport._receipt_cache[receipt] = (
        "antcode:task:ready:worker-1",
        "1-0",
        {"task_id": "task-1", "requeue_count": "not-a-number"},
    )

    assert await transport.requeue_task(receipt, reason="busy") is True

    args = fake_redis.eval.await_args.args
    payload_pairs = dict(zip(args[9::2], args[10::2], strict=False))
    assert payload_pairs["requeue_count"] == "1"
    assert receipt not in transport._receipt_cache


@pytest.mark.asyncio
async def test_requeue_task_over_threshold_settles_into_dead_letter():
    """X1 回归补充：计数可解析时超过阈值必须走统一 DLQ 而不是继续 requeue。"""
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    fake_redis = AsyncMock()
    transport._redis = fake_redis
    dead_letter_owned = AsyncMock()
    transport._reclaimer = MagicMock(dead_letter_owned=dead_letter_owned)
    receipt = "antcode:task:ready:worker-1|1-0"
    transport._receipt_cache[receipt] = (
        "antcode:task:ready:worker-1",
        "1-0",
        {"task_id": "task-1", "requeue_count": str(transport.MAX_REQUEUE_COUNT)},
    )

    assert await transport.requeue_task(receipt, reason="poison") is True

    dead_letter_owned.assert_awaited_once()
    fake_redis.eval.assert_not_awaited()
    assert receipt not in transport._receipt_cache


@pytest.mark.asyncio
async def test_defer_task_keeps_original_message_pending_without_requeue():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    fake_redis = AsyncMock()
    transport._redis = fake_redis
    await transport._deferred_recovery.start()
    receipt = "antcode:task:ready:worker-1|1-0"
    transport._receipt_cache[receipt] = ("antcode:task:ready:worker-1", "1-0", {})

    assert await transport.defer_task(receipt, reason="ownership_contention run_id=run-1") is True
    await transport._deferred_recovery.stop()

    fake_redis.xadd.assert_not_awaited()
    fake_redis.xack.assert_not_awaited()
    assert receipt not in transport._receipt_cache


@pytest.mark.asyncio
async def test_ack_control_returns_false_when_xack_acknowledges_no_messages():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True

    fake_redis = AsyncMock()
    fake_redis.eval.return_value = 0
    transport._redis = fake_redis

    assert await transport.ack_control("antcode:control:worker-1|2-0") is False


def test_decode_data_rejects_invalid_utf8_bytes():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")

    with pytest.raises(UnicodeDecodeError):
        transport._decode_data({b"params": b"\xff"})


def test_decode_data_rejects_invalid_json_fields():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")

    with pytest.raises(ValueError, match="params"):
        transport._decode_data({"params": "{invalid json"})


@pytest.mark.asyncio
async def test_send_log_reports_proto_through_trusted_control_plane():
    control = SimpleNamespace(report_log_batch=AsyncMock(return_value=True))
    transport = RedisTransport(
        redis_url="redis://localhost:6379/0",
        worker_id="worker-1",
        direct_control=control,
    )
    transport._running = True
    transport._lease_id = "lease-1"

    transport._redis = MagicMock()

    log = LogMessage(
        run_id="run-1",
        log_type="stdout",
        content="hello",
        timestamp=datetime.now(),
        sequence=1,
    )

    success = await transport.send_log(log)

    assert success is True
    control.report_log_batch.assert_awaited_once()
    batch = data_pb2.LogBatch.FromString(control.report_log_batch.await_args.args[0])
    assert batch.worker_id == "worker-1"
    assert batch.lease_id == "lease-1"
    assert batch.entries[0].run_id == "run-1"
    assert batch.entries[0].sequence == 1


@pytest.mark.asyncio
async def test_send_log_batch_uses_trusted_control_only():
    control = SimpleNamespace(report_log_batch=AsyncMock(return_value=True))
    transport = RedisTransport(
        redis_url="redis://localhost:6379/0",
        worker_id="worker-1",
        direct_control=control,
    )
    transport._running = True
    transport._lease_id = "lease-1"

    redis = MagicMock()
    transport._redis = redis

    log = LogMessage(
        run_id="run-1",
        log_type="stderr",
        content="hello",
        timestamp=datetime.now(),
        sequence=1,
    )

    assert await transport.send_log_batch([log]) is True
    control.report_log_batch.assert_awaited_once()
    redis.pipeline.assert_not_called()
    batch = data_pb2.LogBatch.FromString(control.report_log_batch.await_args.args[0])
    assert batch.lease_id == "lease-1"
    assert batch.entries[0].run_id == "run-1"
    assert batch.entries[0].sequence == 1
    assert batch.entries[0].log_type == data_pb2.LOG_TYPE_STDERR


@pytest.mark.asyncio
async def test_send_log_batch_reports_generation_loss_after_control_acceptance():
    control = SimpleNamespace(report_log_batch=AsyncMock(return_value=True))
    transport = RedisTransport(
        redis_url="redis://localhost:6379/0",
        worker_id="worker-1",
        direct_control=control,
    )
    transport._running = True
    transport._lease_id = "lease-1"
    transport._lease_fencing_enabled = True
    transport._lease_store = _RuntimeLeaseStore("lease-1")
    transport._lease_store.is_current = AsyncMock(side_effect=[True, False])
    transport._redis = MagicMock()
    log = LogMessage(run_id="run-1", log_type="stdout", content="line", sequence=1)

    assert await transport.send_log_batch([log]) is False
    control.report_log_batch.assert_awaited_once()


def test_log_proto_keeps_stream_type_to_avoid_stdout_stderr_collisions():
    stdout_log = LogMessage(
        run_id="run-1",
        log_type="stdout",
        content="hello",
        timestamp=datetime.now(),
        sequence=1,
    )
    stderr_log = LogMessage(
        run_id="run-1",
        log_type="stderr",
        content="hello",
        timestamp=datetime.now(),
        sequence=1,
    )

    from antcode_worker.transport.log_batches import encode_log_entry

    stdout_entry = encode_log_entry(stdout_log)
    stderr_entry = encode_log_entry(stderr_log)
    assert stdout_entry.log_type == data_pb2.LOG_TYPE_STDOUT
    assert stderr_entry.log_type == data_pb2.LOG_TYPE_STDERR


@pytest.mark.asyncio
async def test_send_log_batch_control_plane_error_is_failure():
    control = SimpleNamespace(
        report_log_batch=AsyncMock(side_effect=RuntimeError("log ingest unavailable")),
    )
    transport = RedisTransport(
        redis_url="redis://localhost:6379/0",
        worker_id="worker-1",
        direct_control=control,
    )
    transport._running = True
    transport._lease_id = "lease-1"

    transport._redis = MagicMock()

    log = LogMessage(
        run_id="run-1",
        log_type="stdout",
        content="hello",
        timestamp=datetime.now(),
        sequence=1,
    )

    assert await transport.send_log_batch([log]) is False


@pytest.mark.asyncio
async def test_send_heartbeat_requires_worker_id():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id=None)
    transport._running = True
    transport._redis = AsyncMock()

    heartbeat = HeartbeatMessage(worker_id="", status="online")
    success = await transport.send_heartbeat(heartbeat)

    assert success is False


def test_transport_uses_custom_namespace_and_group():
    transport = RedisTransport(
        redis_url="redis://localhost:6379/0",
        worker_id="worker-1",
        namespace="ac-test",
        consumer_group="ac-test-workers-alt",
    )

    assert transport._keys.namespace == "ac-test"
    assert transport._consumer_group == "ac-test-workers-alt"
    assert transport._control_group == "ac-test-control"


@pytest.mark.asyncio
async def test_start_log_redacts_redis_password(monkeypatch):
    transport = RedisTransport(
        redis_url="redis://user:secret-password@localhost:6379/0",
        worker_id="worker-1",
        consumer_group="custom-workers",
    )
    fake_redis = AsyncMock()
    transport._control_recovery._channel_index = len(transport._control_channels)
    assert transport._control_recovery.complete is True

    reclaimer_kwargs = {}

    class _Reclaimer:
        def __init__(self, **kwargs):
            reclaimer_kwargs.update(kwargs)

        async def start(self):
            return None

    from antcode_core.infrastructure.redis import factory as redis_factory

    monkeypatch.setattr(redis_factory, "create_async_redis_client", lambda *args, **kwargs: fake_redis)
    ensure_group = AsyncMock()
    monkeypatch.setattr(redis_transport_module, "ensure_consumer_group", ensure_group)
    monkeypatch.setattr(redis_transport_module, "PendingTaskReclaimer", _Reclaimer)
    monkeypatch.setattr(transport, "_set_state", AsyncMock())

    info_messages = []
    monkeypatch.setattr(redis_transport_module.logger, "info", lambda message: info_messages.append(str(message)))

    assert await transport.start() is True
    assert transport._control_recovery.complete is False
    assert reclaimer_kwargs["consumer_group"] == "custom-workers"
    assert [call.args[1:] for call in ensure_group.await_args_list] == [
        ("antcode:task:ready:worker-1", "custom-workers"),
        ("antcode:control:worker-1", "antcode-control"),
    ]

    joined = "\n".join(info_messages)
    assert "secret-password" not in joined
    assert "redis://user:***@localhost:6379/0" in joined
