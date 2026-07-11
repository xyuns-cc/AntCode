"""RedisTransport 现代化行为测试。"""

import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import data_pb2
from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD
from antcode_worker.transport.base import HeartbeatMessage, LogMessage
from antcode_worker.transport.redis.transport import RedisTransport

redis_transport_module = sys.modules[RedisTransport.__module__]


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
async def test_poll_task_rejects_legacy_project_path_even_with_source_bundle():
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

    with pytest.raises(ValueError, match="project_path"):
        await transport.poll_task(timeout=0.1)


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
    fake_redis.xack.return_value = 0
    transport._redis = fake_redis

    receipt = "antcode:task:ready:worker-1|1-0"

    assert await transport.ack_task(receipt, accepted=True) is False
    assert receipt not in transport._receipt_cache


@pytest.mark.asyncio
async def test_ack_control_returns_false_when_xack_acknowledges_no_messages():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True

    fake_redis = AsyncMock()
    fake_redis.xack.return_value = 0
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
async def test_send_log_writes_proto_to_ingest_stream_with_redis_assigned_id():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True

    fake_pipe = _FakePipeline()
    fake_redis = MagicMock()
    fake_redis.pipeline.return_value = fake_pipe
    transport._redis = fake_redis

    log = LogMessage(
        run_id="run-1",
        log_type="stdout",
        content="hello",
        timestamp=datetime.now(),
        sequence=1,
    )

    success = await transport.send_log(log)

    assert success is True
    cmd_names = [item[0] for item in fake_pipe.commands]
    assert cmd_names.count("xadd") == 1
    assert cmd_names.count("expire") == 0
    assert cmd_names.count("execute") == 1
    stream = fake_pipe.commands[0][1][0]
    fields = fake_pipe.commands[0][1][1]
    assert stream == "antcode:log:ingest"
    assert "id" not in fake_pipe.commands[0][2]
    batch = data_pb2.LogBatch.FromString(fields[PROTO_FIELD])
    assert batch.worker_id == "worker-1"
    assert batch.entries[0].run_id == "run-1"
    assert batch.entries[0].sequence == 1


@pytest.mark.asyncio
async def test_send_log_batch_writes_ingest_queue_only():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True

    fake_pipe = _FakePipeline()
    fake_redis = MagicMock()
    fake_redis.pipeline.return_value = fake_pipe
    transport._redis = fake_redis

    log = LogMessage(
        run_id="run-1",
        log_type="stderr",
        content="hello",
        timestamp=datetime.now(),
        sequence=1,
    )

    assert await transport.send_log_batch([log]) is True
    cmd_names = [item[0] for item in fake_pipe.commands]
    assert cmd_names.count("xadd") == 1
    assert cmd_names.count("expire") == 0
    assert cmd_names.count("execute") == 1
    ingest_stream = fake_pipe.commands[0][1][0]
    ingest_fields = fake_pipe.commands[0][1][1]
    assert ingest_stream == "antcode:log:ingest"
    batch = data_pb2.LogBatch.FromString(ingest_fields[PROTO_FIELD])
    assert batch.entries[0].run_id == "run-1"
    assert batch.entries[0].sequence == 1
    assert batch.entries[0].log_type == data_pb2.LOG_TYPE_STDERR


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

    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    stdout_entry = transport._build_log_entry_proto(stdout_log)
    stderr_entry = transport._build_log_entry_proto(stderr_log)
    assert stdout_entry.log_type == data_pb2.LOG_TYPE_STDOUT
    assert stderr_entry.log_type == data_pb2.LOG_TYPE_STDERR


@pytest.mark.asyncio
async def test_send_log_batch_ingest_stream_error_is_failure():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True

    fake_pipe = _FakePipeline(execute_result=[RuntimeError("ID specified in XADD is equal or smaller")])
    fake_redis = MagicMock()
    fake_redis.pipeline.return_value = fake_pipe
    transport._redis = fake_redis

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
    )
    fake_redis = AsyncMock()

    class _Reclaimer:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            return None

    from antcode_core.infrastructure.redis import factory as redis_factory

    monkeypatch.setattr(redis_factory, "create_async_redis_client", lambda *args, **kwargs: fake_redis)
    monkeypatch.setattr(redis_transport_module, "ensure_consumer_group", AsyncMock())
    monkeypatch.setattr(redis_transport_module, "PendingTaskReclaimer", _Reclaimer)
    monkeypatch.setattr(transport, "_set_state", AsyncMock())

    info_messages = []
    monkeypatch.setattr(redis_transport_module.logger, "info", lambda message: info_messages.append(str(message)))

    assert await transport.start() is True

    joined = "\n".join(info_messages)
    assert "secret-password" not in joined
    assert "redis://user:***@localhost:6379/0" in joined
