"""Atomically read live Worker Lease capability snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from antcode_core.application.services.lease_service import LEASE_RECORD_RETENTION_MS, LeaseStore

_READ_LIVE_CAPABILITIES_LUA = """
local snapshots = {}
local retention_ms = tonumber(ARGV[1])
for index = 1, #KEYS, 2 do
    local lease_key = KEYS[index]
    local revoked_key = KEYS[index + 1]
    local lease_id = redis.call('HGET', lease_key, 'lease_id')
    local pttl_ms = redis.call('PTTL', lease_key)
    local current = lease_id and pttl_ms > retention_ms
        and redis.call('SISMEMBER', revoked_key, lease_id) == 0
    table.insert(snapshots, current and lease_id or '')
    table.insert(snapshots, current and (redis.call('HGET', lease_key, 'capabilities_json') or '') or '')
end
return snapshots
"""


@dataclass(frozen=True, slots=True)
class LeaseCapabilitySnapshot:
    lease_id: str
    capabilities_json: str


async def read_live_capability_snapshots(
    redis: Any,
    worker_ids: list[str],
) -> dict[str, LeaseCapabilitySnapshot]:
    if not worker_ids:
        return {}
    store = LeaseStore(redis)
    keys = _snapshot_keys(store, worker_ids)
    values = await redis.eval(
        _READ_LIVE_CAPABILITIES_LUA,
        len(keys),
        *keys,
        str(LEASE_RECORD_RETENTION_MS),
    )
    return _decode_snapshots(worker_ids, values)


def _snapshot_keys(store: LeaseStore, worker_ids: list[str]) -> list[str]:
    keys: list[str] = []
    for worker_id in worker_ids:
        keys.append(store.lease_key(worker_id))
        keys.append(
            store.REVOKED_SET_TEMPLATE.format(
                ns=store.namespace,
                worker_id=worker_id,
            )
        )
    return keys


def _decode_snapshots(worker_ids: list[str], values: list[Any]) -> dict[str, LeaseCapabilitySnapshot]:
    expected = len(worker_ids) * 2
    if len(values) != expected:
        raise RuntimeError("Lease capability snapshot response has an invalid shape")
    result: dict[str, LeaseCapabilitySnapshot] = {}
    for index, worker_id in enumerate(worker_ids):
        lease_id = _text(values[index * 2])
        capabilities = _text(values[index * 2 + 1])
        result[worker_id] = LeaseCapabilitySnapshot(lease_id, capabilities)
    return result


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value or "")


__all__ = ["LeaseCapabilitySnapshot", "read_live_capability_snapshots"]
