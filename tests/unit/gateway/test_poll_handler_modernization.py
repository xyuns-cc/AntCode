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

    async def xautoclaim(self, *args, **kwargs):
        return ("0-0", [], [])


class _FailingRedis:
    async def xgroup_create(self, *args, **kwargs):
        return None

    async def xreadgroup(self, **kwargs):
        raise RuntimeError("redis down")

    async def xautoclaim(self, *args, **kwargs):
        raise RuntimeError("redis down")


class _PendingRedis:
    def __init__(self):
        self.claim_calls = []

    async def xautoclaim(self, *args, **kwargs):
        self.claim_calls.append((args, kwargs))
        return (
            "0-0",
            [
                (
                    "3-0",
                    {
                        "task_id": "task-pending",
                        "project_id": "project-1",
                        "run_id": "run-pending",
                    },
                )
            ],
            [],
        )


class _LegacyPendingRedis:
    """Redis < 7.0 的 XAUTOCLAIM 只返回 (next_id, messages) 两元组。"""

    async def xautoclaim(self, *args, **kwargs):
        return (
            "0-0",
            [
                (
                    "3-0",
                    {
                        "task_id": "task-pending",
                        "project_id": "project-1",
                        "run_id": "run-pending",
                    },
                )
            ],
        )


class _PoisonRedis:
    def __init__(self, *, dlq_fails: bool):
        self.dlq_fails = dlq_fails
        self.acked = []
        self.dead_letters = []

    async def xadd(self, key, payload, **kwargs):
        if self.dlq_fails:
            raise RuntimeError("dlq unavailable")
        self.dead_letters.append((key, payload, kwargs))

    async def xack(self, stream, group, message):
        self.acked.append((stream, group, message))
        return 1


@pytest.mark.asyncio
async def test_consumer_group_init_cached_between_polls():
    redis = _FakeRedis()
    handler = TaskPollHandler(redis_client=redis)

    await handler.handle(worker_id="worker-1", lease_id="lease-1", max_tasks=1, block_ms=1)
    await handler.handle(worker_id="worker-1", lease_id="lease-1", max_tasks=1, block_ms=1)

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
        await handler.handle(worker_id="worker-1", lease_id="lease-1", max_tasks=1, block_ms=1)


@pytest.mark.asyncio
async def test_pending_tasks_require_visibility_timeout_before_redelivery():
    redis = _PendingRedis()
    handler = TaskPollHandler(redis_client=redis)
    stream = task_ready_stream("worker-1")

    results = await handler._drain_pending_streams(
        redis,
        [stream],
        "worker-1",
        max_tasks=1,
    )

    assert results[0][1][0][0] == "3-0"
    args, kwargs = redis.claim_calls[0]
    assert args[:4] == (
        stream,
        TaskPollHandler.WORKER_GROUP,
        "worker-1",
        TaskPollHandler.PENDING_VISIBILITY_TIMEOUT_MS,
    )
    assert kwargs == {"start_id": "0-0", "count": 1}


@pytest.mark.asyncio
async def test_drain_pending_tolerates_two_element_xautoclaim_reply():
    """G2 回归：Redis < 7.0 的两元组 XAUTOCLAIM 返回不应触发解包 ValueError。"""
    redis = _LegacyPendingRedis()
    handler = TaskPollHandler(redis_client=redis)
    stream = task_ready_stream("worker-1")

    results = await handler._drain_pending_streams(
        redis,
        [stream],
        "worker-1",
        max_tasks=1,
    )

    assert results[0][0] == stream
    assert results[0][1][0][0] == "3-0"


@pytest.mark.asyncio
async def test_poison_task_is_acked_only_after_dlq_succeeds():
    stream = task_ready_stream("worker-1")
    results = [(stream, [("1-0", {"project_id": "project-without-task"})])]
    redis = _PoisonRedis(dlq_fails=False)

    tasks = await TaskPollHandler()._tasks_from_results(redis, results, "worker-1")

    assert tasks == []
    assert len(redis.dead_letters) == 1
    assert redis.acked == [(stream, TaskPollHandler.WORKER_GROUP, "1-0")]


@pytest.mark.asyncio
async def test_poison_task_stays_pending_when_dlq_fails():
    stream = task_ready_stream("worker-1")
    results = [(stream, [("1-0", {"project_id": "project-without-task"})])]
    redis = _PoisonRedis(dlq_fails=True)

    tasks = await TaskPollHandler()._tasks_from_results(redis, results, "worker-1")

    assert tasks == []
    assert redis.acked == []
