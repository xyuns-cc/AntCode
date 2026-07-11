"""TaskPollHandler 现代化行为测试。"""

import pytest
from antcode_core.infrastructure.redis import task_ready_stream
from antcode_gateway.handlers.poll import TaskPollHandler


class _FakeRedis:
    def __init__(self):
        self.created = []
        self.xreadgroup_calls = 0

    async def xgroup_create(self, stream_key, group, id="0", mkstream=True):
        self.created.append((stream_key, group, id, mkstream))

    async def xreadgroup(self, **kwargs):
        self.xreadgroup_calls += 1
        if self.xreadgroup_calls == 1:
            return [
                (
                    task_ready_stream("worker-1"),
                    [
                        (
                            "1-0",
                            {
                                "task_id": "task-1",
                                "project_id": "proj-1",
                                "execution_id": "legacy-run",
                            },
                        )
                    ],
                )
            ]
        return []


class _FailingRedis:
    async def xgroup_create(self, *args, **kwargs):
        return None

    async def xreadgroup(self, **kwargs):
        raise RuntimeError("redis down")


class _AckMissRedis:
    async def xack(self, *args, **kwargs):
        return 0


@pytest.mark.asyncio
async def test_consumer_group_init_cached_between_polls():
    redis = _FakeRedis()
    handler = TaskPollHandler(redis_client=redis)

    await handler.handle(worker_id="worker-1", max_tasks=1, block_ms=1)
    await handler.handle(worker_id="worker-1", max_tasks=1, block_ms=1)

    assert len(redis.created) == 1
    assert redis.created[0][0] == task_ready_stream("worker-1")


def test_parse_task_data_ignores_legacy_execution_id():
    handler = TaskPollHandler(redis_client=None)
    task = handler._parse_task_data(
        data={
            "task_id": "task-1",
            "project_id": "proj-1",
            "execution_id": "legacy-run",
        },
        message_id="1-0",
    )

    assert task is not None
    assert task.run_id == ""


def test_parse_task_data_keeps_dict_params_environment():
    handler = TaskPollHandler(redis_client=None)
    task = handler._parse_task_data(
        data={
            "task_id": "task-2",
            "project_id": "proj-2",
            "params": {"depth": 3},
            "environment": {"A": "B"},
        },
        message_id="2-0",
    )

    assert task is not None
    assert task.params == {"depth": 3}
    assert task.environment == {"A": "B"}


def test_parse_task_data_rejects_invalid_params_json():
    handler = TaskPollHandler(redis_client=None)

    with pytest.raises(ValueError, match="params"):
        handler._parse_task_data(
            data={
                "task_id": "task-3",
                "project_id": "proj-3",
                "params": "{invalid",
            },
            message_id="3-0",
        )


def test_parse_task_data_rejects_invalid_environment_json():
    handler = TaskPollHandler(redis_client=None)

    with pytest.raises(ValueError, match="environment"):
        handler._parse_task_data(
            data={
                "task_id": "task-4",
                "project_id": "proj-4",
                "environment": "{invalid",
            },
            message_id="4-0",
        )


@pytest.mark.asyncio
async def test_handle_exposes_redis_read_failures():
    handler = TaskPollHandler(redis_client=_FailingRedis())

    with pytest.raises(RuntimeError, match="redis down"):
        await handler.handle(worker_id="worker-1", max_tasks=1, block_ms=1)


@pytest.mark.asyncio
async def test_ack_task_returns_false_when_redis_ack_misses_message():
    handler = TaskPollHandler(redis_client=_AckMissRedis())

    success = await handler.ack_task(
        worker_id="worker-1",
        queue=task_ready_stream("worker-1"),
        message_id="missing-id",
    )

    assert success is False
