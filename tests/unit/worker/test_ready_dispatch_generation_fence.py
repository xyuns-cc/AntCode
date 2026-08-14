from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.common.security.task_payload_envelope import seal_ready_payload
from antcode_worker.transport.redis.transport import RedisTransport

WORKER_SECRET = "direct-ready-generation-secret-material-0001"


def _task_fields(dispatch_lease_id: str | None, dispatch_lease_gen: object | None = None) -> dict[str, object]:
    digest = "a" * 64
    fields = {
        "task_id": "task-1",
        "project_id": "proj-1",
        "source_bundle_uri": f"pgartifact://{digest}",
        "source_bundle_sha256": digest,
        "source_bundle_size": "1",
    }
    if dispatch_lease_id is not None:
        fields["dispatch_lease_id"] = dispatch_lease_id
    if dispatch_lease_gen is not None:
        fields["dispatch_lease_gen"] = dispatch_lease_gen
    return fields


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch_lease_id", [None, "lease-old", "lease-1"])
async def test_direct_poll_dead_letters_wrong_dispatch_generation(dispatch_lease_id):
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    transport._lease_id = "lease-1"
    transport._require_current_generation = AsyncMock()
    redis = AsyncMock()
    redis.xreadgroup.return_value = [
        (
            "{antcode}:task:ready:worker-1",
            [("2-0", _task_fields(dispatch_lease_id))],
        )
    ]
    transport._redis = redis
    transport._reclaimer = MagicMock(dead_letter_owned=AsyncMock())

    assert await transport.poll_task(timeout=0.1) is None

    payload = transport._reclaimer.dead_letter_owned.await_args.args[2]
    assert "dispatch_lease_" in payload["_bad_frame_error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch_lease_gen", [True, 7.0, " 7", "07", "0", "invalid"])
async def test_direct_poll_dead_letters_noncanonical_generation(dispatch_lease_gen):
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    transport._lease_id = "lease-1"
    transport._require_current_generation = AsyncMock()
    redis = AsyncMock()
    redis.xreadgroup.return_value = [
        (
            "{antcode}:task:ready:worker-1",
            [("2-0", _task_fields("lease-1", dispatch_lease_gen))],
        )
    ]
    transport._redis = redis
    transport._reclaimer = MagicMock(dead_letter_owned=AsyncMock())

    assert await transport.poll_task(timeout=0.1) is None
    transport._reclaimer.dead_letter_owned.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_poll_dead_letters_wrong_payload_key_without_plaintext():
    transport = RedisTransport(
        redis_url="redis://localhost:6379/0",
        worker_id="worker-1",
        task_payload_secret=WORKER_SECRET,
    )
    transport._running = True
    transport._lease_id = "lease-1"
    transport._require_current_generation = AsyncMock()
    frame = seal_ready_payload(
        {
            **_task_fields("lease-1", "7"),
            "run_id": "run-1",
            "params": {"token": "direct-dlq-secret-sentinel"},
        },
        worker_id="worker-1",
        worker_secret="wrong-direct-ready-secret-material-0001",
    )
    redis = AsyncMock()
    redis.xreadgroup.return_value = [("{antcode}:task:ready:worker-1", [("3-0", frame)])]
    transport._redis = redis
    transport._reclaimer = MagicMock(dead_letter_owned=AsyncMock())

    assert await transport.poll_task(timeout=0.1) is None

    payload = transport._reclaimer.dead_letter_owned.await_args.args[2]
    assert "params" not in payload
    assert "direct-dlq-secret-sentinel" not in str(payload)
    assert "TaskPayloadDecryptionError" in payload["_bad_frame_error"]


@pytest.mark.asyncio
async def test_direct_poll_rejects_legacy_plaintext_payload_and_redacts_dlq():
    transport = RedisTransport(
        redis_url="redis://localhost:6379/0",
        worker_id="worker-1",
        task_payload_secret=WORKER_SECRET,
    )
    transport._running = True
    transport._lease_id = "lease-1"
    transport._require_current_generation = AsyncMock()
    legacy = {
        **_task_fields("lease-1", "7"),
        "run_id": "run-1",
        "params": {"token": "legacy-direct-secret-sentinel"},
        "environment": {"API_KEY": "legacy-direct-env-sentinel"},
    }
    redis = AsyncMock()
    redis.xreadgroup.return_value = [("{antcode}:task:ready:worker-1", [("3-0", legacy)])]
    transport._redis = redis
    transport._reclaimer = MagicMock(dead_letter_owned=AsyncMock())

    assert await transport.poll_task(timeout=0.1) is None

    payload = transport._reclaimer.dead_letter_owned.await_args.args[2]
    assert "params" not in payload
    assert "environment" not in payload
    assert "legacy-direct-secret-sentinel" not in str(payload)
    assert "legacy-direct-env-sentinel" not in str(payload)
    assert "TaskPayloadEnvelopeError" in payload["_bad_frame_error"]


@pytest.mark.asyncio
async def test_direct_requeue_persists_original_ciphertext_not_decrypted_fields():
    transport = RedisTransport(
        redis_url="redis://localhost:6379/0",
        worker_id="worker-1",
        task_payload_secret=WORKER_SECRET,
    )
    transport._running = True
    transport._lease_id = "lease-1"
    transport._require_current_generation = AsyncMock()
    frame = seal_ready_payload(
        {
            **_task_fields("lease-1", "7"),
            "run_id": "run-1",
            "params": {"token": "direct-requeue-secret-sentinel"},
        },
        worker_id="worker-1",
        worker_secret=WORKER_SECRET,
    )
    redis = AsyncMock()
    redis.time.return_value = (1_000, 0)
    redis.eval.return_value = [1, "4-0"]
    transport._redis = redis

    task = await transport._build_guarded_task(
        "{antcode}:task:ready:worker-1",
        "3-0",
        frame,
    )
    assert task is not None
    assert task.params == {"token": "direct-requeue-secret-sentinel"}

    assert await transport.requeue_task(task.receipt, reason="busy") is True
    arguments = redis.eval.await_args.args
    persisted_fields = dict(zip(arguments[9::2], arguments[10::2], strict=False))
    assert "params" not in persisted_fields
    assert "environment" not in persisted_fields
    assert "sensitive_payload_envelope" in persisted_fields
    assert "direct-requeue-secret-sentinel" not in str(arguments)
