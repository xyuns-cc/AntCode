"""B12: 调度代际栅栏必须显式传入，Lua 侧对空 token fail-closed。

修复前 ``scheduler_fencing_token`` 可选、Lua 用 ``if ARGV[5] ~= ''`` 包住整段
Master 代际校验，任何没被 ``scheduler_dispatch_epoch()`` 包裹的派发路径都会
静默退化成"无 Master 栅栏"，脑裂时旧 Master 仍能向 Worker 投递任务。

环境未安装 lupa/fakeredis，无法执行真 Lua；这里用一个按
``_PUBLISH_READY_LUA`` 契约求值的假 Redis 驱动真实的 ``publish_ready_batch``
与 ``WorkerTaskDispatcher._send_batch_to_queue``，并额外对脚本正文做结构断言，
防止假 Redis 与真脚本发生分歧。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot
from antcode_core.application.services.lease_fenced_ready_publish import (
    _PUBLISH_READY_LUA,
    SchedulerDispatchEpochMissingError,
    SchedulerDispatchFenceError,
    publish_ready_batch,
    scheduler_dispatch_epoch,
)
from antcode_core.application.services.lease_service import LEASE_RECORD_RETENTION_MS
from antcode_core.application.services.workers import worker_dispatcher as dispatcher_module
from antcode_core.application.services.workers.worker_dispatcher import WorkerTaskDispatcher
from antcode_core.infrastructure.redis import stream_client as stream_client_module
from redis.exceptions import ResponseError

NAMESPACE = "tenant"
WORKER_ID = "worker-1"
WORKER_SECRET = "worker-ready-publish-secret-material-0001"
LEASE_ID = "lease-1"
LEASE_GEN = 7
CAPABILITIES_JSON = '{"task_types":["code"]}'
LIVE_PTTL_MS = 30_000
CURRENT_EPOCH = 19
STALE_EPOCH = 18
REJECTED_EPOCH = 0
KEY_COUNT = 4
# ARGV 下标（0 基），与 _PUBLISH_READY_LUA 的 ARGV[1..6] 一一对应。
ARG_RETENTION = 0
ARG_LEASE_ID = 1
ARG_CAPABILITIES = 2
ARG_LEASE_GEN = 3
ARG_SCHEDULER_TOKEN = 4
ARG_MESSAGE_COUNT = 5
SINGLE_MESSAGE = 1


def _snapshot() -> LeaseCapabilitySnapshot:
    return LeaseCapabilitySnapshot(LEASE_ID, CAPABILITIES_JSON, LEASE_GEN)


def _worker() -> SimpleNamespace:
    return SimpleNamespace(id=7, public_id=WORKER_ID, name="Worker 1")


def _task() -> dict[str, Any]:
    return {"task_id": "run-1", "run_id": "run-1", "project_id": "p-1"}


class FenceScriptRedis:
    """按 ``_PUBLISH_READY_LUA`` 契约求值的假 Redis。"""

    def __init__(self, *, scheduler_token: str) -> None:
        self.lease = {
            "lease_id": LEASE_ID,
            "capabilities_json": CAPABILITIES_JSON,
            "sequence": str(LEASE_GEN),
        }
        self.pttl_ms = LIVE_PTTL_MS
        self.revoked: set[str] = set()
        self.scheduler_token = scheduler_token
        self.keys: list[str] = []
        self.published: list[list[str]] = []

    async def eval(self, script: str, key_count: int, *arguments: Any) -> list[str]:
        assert script == _PUBLISH_READY_LUA, "假 Redis 只对当前脚本正文有效"
        self.keys = [str(key) for key in arguments[:key_count]]
        argv = [str(value) for value in arguments[key_count:]]
        rejection = self._fence_outcome(argv)
        if rejection:
            return [rejection]
        return self._xadd_batch(argv)

    def _fence_outcome(self, argv: list[str]) -> str:
        """复刻脚本的栅栏判定顺序；空 token 是脚本级错误而非跳过校验。"""
        lease_checks = (
            (self.lease.get("lease_id") != argv[ARG_LEASE_ID], "lease_changed"),
            (self.pttl_ms <= int(argv[ARG_RETENTION]), "lease_expired"),
            (self.lease["lease_id"] in self.revoked, "lease_revoked"),
            (self.lease.get("capabilities_json", "") != argv[ARG_CAPABILITIES], "capabilities_changed"),
            (int(self.lease.get("sequence", "0")) != int(argv[ARG_LEASE_GEN]), "lease_generation_changed"),
        )
        for rejected, outcome in lease_checks:
            if rejected:
                return outcome
        if not argv[ARG_SCHEDULER_TOKEN]:
            raise ResponseError("scheduler fencing token is required")
        return "" if self.scheduler_token == argv[ARG_SCHEDULER_TOKEN] else "scheduler_generation_changed"

    def _xadd_batch(self, argv: list[str]) -> list[str]:
        message_ids = ["ok"]
        cursor = ARG_MESSAGE_COUNT + 1
        for index in range(int(argv[ARG_MESSAGE_COUNT])):
            field_count = int(argv[cursor])
            cursor += 1
            self.published.append(argv[cursor : cursor + field_count * 2])
            cursor += field_count * 2
            message_ids.append(f"1-{index}")
        return message_ids


def _lua_arguments(token: str) -> list[str]:
    """构造一批最小合法 ARGV，只让 scheduler token 位可变。"""
    return [
        str(LEASE_RECORD_RETENTION_MS),
        LEASE_ID,
        CAPABILITIES_JSON,
        str(LEASE_GEN),
        token,
        str(SINGLE_MESSAGE),
        str(SINGLE_MESSAGE),
        "task_id",
        "run-1",
    ]


async def _publish(redis: FenceScriptRedis, token: int) -> list[str]:
    return await publish_ready_batch(
        redis,
        worker_id=WORKER_ID,
        snapshot=_snapshot(),
        messages=[{"task_id": "run-1"}],
        scheduler_fencing_token=token,
        namespace=NAMESPACE,
        worker_secret=WORKER_SECRET,
    )


def test_lua_rejects_empty_scheduler_token_before_reading_the_fencing_key() -> None:
    """脚本正文必须 fail-closed：空 token 直接 error_reply，不再跳过校验。"""
    guard = _PUBLISH_READY_LUA.index("if ARGV[5] == '' then")
    reply = _PUBLISH_READY_LUA.index("return redis.error_reply('scheduler fencing token is required')")
    fence_read = _PUBLISH_READY_LUA.index("redis.call('GET', KEYS[4])")

    assert guard < reply < fence_read
    assert "if ARGV[5] ~= '' then" not in _PUBLISH_READY_LUA


@pytest.mark.asyncio
async def test_empty_scheduler_token_is_a_script_error_not_a_skipped_check() -> None:
    redis = FenceScriptRedis(scheduler_token=str(CURRENT_EPOCH))

    with pytest.raises(ResponseError, match="scheduler fencing token is required"):
        await redis.eval(_PUBLISH_READY_LUA, KEY_COUNT, *"abcd", *_lua_arguments(""))

    assert redis.published == []


def test_publish_ready_batch_requires_an_explicit_scheduler_token() -> None:
    with pytest.raises(TypeError, match="scheduler_fencing_token"):
        publish_ready_batch(
            FenceScriptRedis(scheduler_token=str(CURRENT_EPOCH)),
            worker_id=WORKER_ID,
            snapshot=_snapshot(),
            messages=[{"task_id": "run-1"}],
            worker_secret=WORKER_SECRET,
        )


@pytest.mark.asyncio
async def test_non_positive_scheduler_token_never_reaches_redis() -> None:
    redis = FenceScriptRedis(scheduler_token=str(CURRENT_EPOCH))

    with pytest.raises(ValueError, match="positive"):
        await _publish(redis, REJECTED_EPOCH)

    assert redis.keys == []
    assert redis.published == []


@pytest.mark.asyncio
async def test_stale_master_epoch_is_rejected_without_publishing() -> None:
    redis = FenceScriptRedis(scheduler_token=str(CURRENT_EPOCH))

    with pytest.raises(SchedulerDispatchFenceError, match="scheduler_generation_changed"):
        await _publish(redis, STALE_EPOCH)

    assert redis.published == []


@pytest.mark.asyncio
async def test_missing_fencing_key_is_rejected_without_publishing() -> None:
    """Master 从未写过代际键时也必须拒绝，而不是当作"无需校验"。"""
    redis = FenceScriptRedis(scheduler_token="")

    with pytest.raises(SchedulerDispatchFenceError):
        await _publish(redis, CURRENT_EPOCH)

    assert redis.published == []


@pytest.mark.asyncio
async def test_matching_master_epoch_still_publishes() -> None:
    redis = FenceScriptRedis(scheduler_token=str(CURRENT_EPOCH))

    message_ids = await _publish(redis, CURRENT_EPOCH)

    assert message_ids == ["1-0"]
    assert len(redis.published) == SINGLE_MESSAGE
    assert redis.keys[-1] == f"{{{NAMESPACE}}}:fencing:dispatch:master"


class _FakeStream:
    async def xgroup_create(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    async def xpending(self, *_args: Any, **_kwargs: Any) -> dict[str, int]:
        return {"pending_count": 0}

    async def _get_client(self) -> object:
        return object()


def _patch_dispatch_publish(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    monkeypatch.setattr(stream_client_module, "StreamClient", lambda *_a, **_k: _FakeStream())

    async def publish(_redis: Any, **kwargs: Any) -> list[str]:
        published.append(kwargs)
        return ["1-0"]

    published: list[dict[str, Any]] = []
    monkeypatch.setattr(dispatcher_module.ready_publish, "publish_ready_batch", publish)
    return published


@pytest.mark.asyncio
async def test_dispatch_outside_scheduler_epoch_raises_instead_of_publishing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = _patch_dispatch_publish(monkeypatch)

    with pytest.raises(SchedulerDispatchEpochMissingError, match="scheduler_dispatch_epoch"):
        await WorkerTaskDispatcher()._send_batch_to_queue(
            worker=_worker(),
            tasks=[_task()],
            batch_id="b1",
            lease_snapshot=_snapshot(),
        )

    assert published == []


@pytest.mark.asyncio
async def test_dispatch_inside_scheduler_epoch_forwards_the_master_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = _patch_dispatch_publish(monkeypatch)

    with scheduler_dispatch_epoch(CURRENT_EPOCH):
        result = await WorkerTaskDispatcher()._send_batch_to_queue(
            worker=_worker(),
            tasks=[_task()],
            batch_id="b1",
            lease_snapshot=_snapshot(),
        )

    assert result["success"] is True
    assert published[0]["scheduler_fencing_token"] == CURRENT_EPOCH
