from __future__ import annotations

import pytest
from antcode_core.common.security.task_payload_envelope import seal_ready_payload
from antcode_core.infrastructure.redis import task_ready_stream
from antcode_gateway.handlers.poll import TaskPollHandler

WORKER_SECRET = "gateway-ready-payload-secret-material-0001"


def _handler() -> TaskPollHandler:
    return TaskPollHandler()


def _seal(payload: dict[str, object], *, secret: str = WORKER_SECRET) -> dict[str, object]:
    return seal_ready_payload(payload, worker_id="worker-1", worker_secret=secret)


class _Redis:
    def __init__(self) -> None:
        self.acked: list[tuple[str, str, str]] = []
        self.dead_letters: list[tuple[str, dict, dict]] = []

    async def xadd(self, key: str, payload: dict, **kwargs) -> None:
        self.dead_letters.append((key, payload, kwargs))

    async def xack(self, stream: str, group: str, message: str) -> int:
        self.acked.append((stream, group, message))
        return 1


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch_lease_id", [None, "lease-old", "lease-1"])
async def test_wrong_generation_is_dead_lettered_and_acked(dispatch_lease_id):
    stream = task_ready_stream("worker-1")
    data = {"task_id": "task-1", "project_id": "project-1"}
    if dispatch_lease_id is not None:
        data["dispatch_lease_id"] = dispatch_lease_id
    redis = _Redis()

    tasks = await _handler()._tasks_from_results(
        redis,
        [(stream, [("2-0", data)])],
        "worker-1",
        lease_id="lease-1",
    )

    assert tasks == []
    assert redis.dead_letters[0][1]["dead_letter_reason"] == "parse_error:ValueError"
    assert redis.acked == [(stream, TaskPollHandler.WORKER_GROUP, "2-0")]


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch_lease_gen", [True, 7.0, " 7", "07", "0", "invalid"])
async def test_noncanonical_generation_is_dead_lettered_and_acked(dispatch_lease_gen):
    stream = task_ready_stream("worker-1")
    data = {
        "task_id": "task-1",
        "project_id": "project-1",
        "dispatch_lease_id": "lease-1",
        "dispatch_lease_gen": dispatch_lease_gen,
    }
    redis = _Redis()

    tasks = await _handler()._tasks_from_results(
        redis,
        [(stream, [("2-0", data)])],
        "worker-1",
        lease_id="lease-1",
    )

    assert tasks == []
    assert redis.acked == [(stream, TaskPollHandler.WORKER_GROUP, "2-0")]


@pytest.mark.asyncio
async def test_current_generation_is_delivered():
    stream = task_ready_stream("worker-1")
    data = _seal(
        {
            "task_id": "task-1",
            "project_id": "project-1",
            "dispatch_lease_id": "lease-1",
            "dispatch_lease_gen": "7",
        }
    )

    tasks = await _handler()._tasks_from_results(
        _Redis(),
        [(stream, [("3-0", data)])],
        "worker-1",
        lease_id="lease-1",
    )

    assert [task.task_id for task in tasks] == ["task-1"]


@pytest.mark.asyncio
async def test_gateway_forwards_wrong_payload_key_without_decrypting() -> None:
    stream = task_ready_stream("worker-1")
    data = _seal(
        {
            "task_id": "task-1",
            "project_id": "project-1",
            "dispatch_lease_id": "lease-1",
            "dispatch_lease_gen": "7",
            "params": {"token": "must-not-reach-dlq"},
        },
        secret="different-gateway-ready-secret-material",
    )
    redis = _Redis()

    tasks = await _handler()._tasks_from_results(
        redis,
        [(stream, [("4-0", data)])],
        "worker-1",
        lease_id="lease-1",
    )

    assert [task.task_id for task in tasks] == ["task-1"]
    assert "must-not-reach-dlq" not in tasks[0].sealed_ready_payload.decode()
    assert redis.dead_letters == []
    assert redis.acked == []


@pytest.mark.asyncio
async def test_gateway_rejects_legacy_plaintext_payload_and_redacts_dlq() -> None:
    stream = task_ready_stream("worker-1")
    legacy = {
        "task_id": "task-1",
        "run_id": "run-1",
        "project_id": "project-1",
        "dispatch_lease_id": "lease-1",
        "dispatch_lease_gen": "7",
        "params": {"token": "legacy-gateway-secret-sentinel"},
        "environment": {"API_KEY": "legacy-gateway-env-sentinel"},
    }
    redis = _Redis()

    tasks = await _handler()._tasks_from_results(
        redis,
        [(stream, [("5-0", legacy)])],
        "worker-1",
        lease_id="lease-1",
    )

    assert tasks == []
    dead_payload = redis.dead_letters[0][1]
    assert "params" not in dead_payload
    assert "environment" not in dead_payload
    assert "legacy-gateway-secret-sentinel" not in str(dead_payload)
    assert "legacy-gateway-env-sentinel" not in str(dead_payload)
    assert dead_payload["dead_letter_reason"] == "parse_error:ValueError"
    assert redis.acked == [(stream, TaskPollHandler.WORKER_GROUP, "5-0")]
