from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers.worker_dispatcher import (
    BatchDispatchResult,
    WorkerTaskDispatcher,
)
from antcode_gateway.handlers.poll import TaskPollHandler, task_info_to_dispatch
from antcode_worker.engine.engine import Engine
from antcode_worker.transport.gateway.codecs import TaskDecoder
from antcode_worker.transport.redis.transport import RedisTransport


@pytest.mark.asyncio
async def test_dispatch_uses_trusted_runtime_field_and_strips_reserved_env(monkeypatch) -> None:
    dispatcher = WorkerTaskDispatcher()
    dispatch_batch = AsyncMock(return_value=BatchDispatchResult(success=True))
    monkeypatch.setattr(dispatcher, "dispatch_batch", dispatch_batch)

    await dispatcher.dispatch_task(
        project_id="project-1",
        run_id="run-1",
        environment_vars={"ANTCODE_RUNTIME_ENV": "attacker-private", "SAFE": "value"},
        runtime_env_name="project-bound-runtime",
    )

    task = dispatch_batch.await_args.kwargs["tasks"][0]
    assert task["runtime_env_name"] == "project-bound-runtime"
    assert task["environment"] == {"SAFE": "value"}


def test_gateway_runtime_field_round_trip_is_separate_from_environment() -> None:
    handler = TaskPollHandler(redis_client=None)
    task_info = handler._parse_task_data(
        {
            "task_id": "task-1",
            "project_id": "project-1",
            "project_type": "rule",
            "runtime_env_name": "trusted-runtime",
            "environment": {"ANTCODE_RUNTIME_ENV": "attacker-private", "SAFE": "value"},
        },
        "1-0",
    )

    message = TaskDecoder.decode(task_info_to_dispatch(task_info))
    payload = Engine.__new__(Engine)._build_payload(message)

    assert message.runtime_env_name == "trusted-runtime"
    assert payload.env_vars == {"SAFE": "value"}


def test_direct_redis_transport_preserves_trusted_runtime_field() -> None:
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")

    message = transport._build_task_message(
        "stream-1",
        "1-0",
        {
            "task_id": "task-1",
            "project_id": "project-1",
            "project_type": "rule",
            "runtime_env_name": "trusted-runtime",
        },
        "stream-1|1-0",
    )

    assert message.runtime_env_name == "trusted-runtime"
