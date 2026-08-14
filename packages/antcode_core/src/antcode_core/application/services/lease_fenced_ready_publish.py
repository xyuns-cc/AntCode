"""Atomically fence a Worker Lease and publish its ready-stream batch."""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Any

import ujson

from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot
from antcode_core.application.services.lease_service import LEASE_RECORD_RETENTION_MS, LeaseStore
from antcode_core.common.security.task_payload_envelope import seal_ready_payload
from antcode_core.common.security.worker_auth import load_worker_secret
from antcode_core.infrastructure.redis.control_plane import (
    redis_namespace,
    scheduler_dispatch_fencing_key,
    task_ready_stream,
)

_PUBLISH_READY_LUA = """
local lease_id = redis.call('HGET', KEYS[1], 'lease_id')
if not lease_id or lease_id ~= ARGV[2] then
    return {'lease_changed'}
end
if redis.call('PTTL', KEYS[1]) <= tonumber(ARGV[1]) then
    return {'lease_expired'}
end
if redis.call('SISMEMBER', KEYS[2], lease_id) ~= 0 then
    return {'lease_revoked'}
end
local capabilities_json = redis.call('HGET', KEYS[1], 'capabilities_json') or ''
if capabilities_json ~= ARGV[3] then
    return {'capabilities_changed'}
end
local lease_gen = tonumber(redis.call('HGET', KEYS[1], 'sequence') or '0')
if lease_gen ~= tonumber(ARGV[4]) then
    return {'lease_generation_changed'}
end
-- B12: Master 代际栅栏 fail-closed。空 token 曾经会整段跳过校验，
-- 让未绑定代际的派发路径静默退化成"无 Master 栅栏"；现在直接报错。
if ARGV[5] == '' then
    return redis.error_reply('scheduler fencing token is required')
end
local scheduler_token = redis.call('GET', KEYS[4])
if not scheduler_token or scheduler_token ~= ARGV[5] then
    return {'scheduler_generation_changed'}
end

local message_count = tonumber(ARGV[6])
if not message_count or message_count < 1 then
    return redis.error_reply('ready batch must contain messages')
end
local cursor = 7
local batch = {}
for message_index = 1, message_count do
    local field_count = tonumber(ARGV[cursor])
    cursor = cursor + 1
    if not field_count or field_count < 1 then
        return redis.error_reply('ready message must contain fields')
    end
    local fields = {}
    for field_index = 1, field_count * 2 do
        fields[field_index] = ARGV[cursor]
        cursor = cursor + 1
    end
    batch[message_index] = fields
end
if cursor - 1 ~= #ARGV then
    return redis.error_reply('ready batch argument shape is invalid')
end
local ids = {'ok'}
for message_index = 1, message_count do
    table.insert(ids, redis.call('XADD', KEYS[3], '*', unpack(batch[message_index])))
end
return ids
"""

_SCHEDULER_FENCING_TOKEN: ContextVar[int | None] = ContextVar(
    "scheduler_fencing_token",
    default=None,
)

# Lease 代际与 Master 代际都是从 1 开始的单调计数器，0 与负数一律非法。
MIN_GENERATION = 1
# Lua 脚本固定使用 4 个 KEYS（lease / revoked / ready stream / 代际栅栏）。
_PUBLISH_READY_KEY_COUNT = 4


class LeaseDispatchFenceError(RuntimeError):
    """The selected Lease/capability generation changed before publish."""


class SchedulerDispatchFenceError(LeaseDispatchFenceError):
    """The Master scheduler generation changed before publish."""


class SchedulerDispatchEpochMissingError(SchedulerDispatchFenceError):
    """派发调用树没有绑定 Master 代际，禁止退化为无栅栏发布。"""


@contextlib.contextmanager
def scheduler_dispatch_epoch(token: int):
    """Bind a verified Master epoch to one asynchronous dispatch call tree."""
    if token < MIN_GENERATION:
        raise ValueError("scheduler fencing token must be positive")
    marker = _SCHEDULER_FENCING_TOKEN.set(token)
    try:
        yield
    finally:
        _SCHEDULER_FENCING_TOKEN.reset(marker)


def require_scheduler_dispatch_epoch() -> int:
    """取当前派发代际；不在 ``scheduler_dispatch_epoch()`` 内即显式报错。

    B12: 这里是唯一允许读 ContextVar 的入口。取不到代际必须是错误，
    不能返回 None 让调用方带着空 token 继续把任务写进 ready stream。
    """
    token = _SCHEDULER_FENCING_TOKEN.get()
    if token is None:
        raise SchedulerDispatchEpochMissingError(
            "ready publish requires an active scheduler_dispatch_epoch() Master epoch"
        )
    return token


def raise_if_dispatch_fenced(error: Exception) -> None:
    if isinstance(error, LeaseDispatchFenceError):
        raise error


async def publish_ready_batch(
    redis: Any,
    *,
    worker_id: str,
    snapshot: LeaseCapabilitySnapshot,
    messages: list[dict[str, Any]],
    scheduler_fencing_token: int,
    namespace: str | None = None,
    worker_secret: str | None = None,
) -> list[str]:
    """在同一段 Lua 内校验 Lease/能力/Master 代际后原子发布 ready 批次。

    B12: ``scheduler_fencing_token`` 必传。调用方必须先从
    ``require_scheduler_dispatch_epoch()`` 取到已验证的 Master 代际。
    """
    if not worker_id or not snapshot.lease_id or not snapshot.capabilities_json:
        raise ValueError("ready publish requires a complete Lease capability snapshot")
    if snapshot.lease_gen < MIN_GENERATION:
        raise ValueError("ready publish requires a positive Lease generation")
    if not messages:
        raise ValueError("ready publish requires at least one message")
    if scheduler_fencing_token < MIN_GENERATION:
        raise ValueError("scheduler fencing token must be positive")
    secret = worker_secret or await load_worker_secret(worker_id)
    if not secret:
        raise RuntimeError(f"ready publish Worker payload key is unavailable: {worker_id}")
    # 派发热路径上不再为拼 key 构造 LeaseStore（每次都会新建 Event/Lock），
    # 直接复用其 key 模板；key 文本与 LeaseStore 完全一致。
    ns = redis_namespace(namespace)
    result = await redis.eval(
        _PUBLISH_READY_LUA,
        _PUBLISH_READY_KEY_COUNT,
        LeaseStore.LEASE_KEY_TEMPLATE.format(ns=ns, worker_id=worker_id),
        LeaseStore.REVOKED_SET_TEMPLATE.format(ns=ns, worker_id=worker_id),
        task_ready_stream(worker_id, namespace=ns),
        scheduler_dispatch_fencing_key(namespace=ns),
        str(LEASE_RECORD_RETENTION_MS),
        snapshot.lease_id,
        snapshot.capabilities_json,
        str(snapshot.lease_gen),
        str(scheduler_fencing_token),
        str(len(messages)),
        *_encode_messages(
            messages,
            snapshot=snapshot,
            worker_id=worker_id,
            worker_secret=secret,
        ),
    )
    return _require_published(result, expected_count=len(messages))


def _encode_messages(
    messages: list[dict[str, Any]],
    *,
    snapshot: LeaseCapabilitySnapshot,
    worker_id: str,
    worker_secret: str,
) -> list[str | bytes]:
    encoded: list[str | bytes] = []
    for message in messages:
        if not message:
            raise ValueError("ready message must contain at least one field")
        if "dispatch_lease_id" in message or "dispatch_lease_gen" in message:
            raise ValueError("ready message dispatch fence is assigned by the publisher")
        fenced_message = seal_ready_payload(
            {
                **message,
                "dispatch_lease_id": snapshot.lease_id,
                "dispatch_lease_gen": snapshot.lease_gen,
            },
            worker_id=worker_id,
            worker_secret=worker_secret,
        )
        encoded.append(str(len(fenced_message)))
        for name, value in fenced_message.items():
            encoded.append(str(name))
            encoded.append(value if isinstance(value, (str, bytes)) else ujson.dumps(value))
    return encoded


def _require_published(result: Any, *, expected_count: int) -> list[str]:
    if not isinstance(result, (list, tuple)) or not result:
        raise RuntimeError("ready publish returned an invalid response")
    values = [_text(value) for value in result]
    if values[0] == "scheduler_generation_changed":
        raise SchedulerDispatchFenceError(
            "Master scheduler generation changed before ready publish: scheduler_generation_changed"
        )
    if values[0] != "ok":
        raise LeaseDispatchFenceError(f"Worker Lease/capability changed before ready publish: {values[0]}")
    if len(values) != expected_count + 1 or any(not message_id for message_id in values[1:]):
        raise RuntimeError("ready publish returned invalid message ids")
    return values[1:]


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value or "")


__all__ = [
    "MIN_GENERATION",
    "LeaseDispatchFenceError",
    "SchedulerDispatchEpochMissingError",
    "SchedulerDispatchFenceError",
    "publish_ready_batch",
    "raise_if_dispatch_fenced",
    "require_scheduler_dispatch_epoch",
    "scheduler_dispatch_epoch",
]
