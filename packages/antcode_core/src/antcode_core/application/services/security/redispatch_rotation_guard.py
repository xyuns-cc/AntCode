"""Offline drain guard for ENCRYPTION_KEY-protected redispatch payloads."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RedispatchDrainState:
    pending: int
    processing: int
    legacy_pending: int
    legacy_processing: int

    @property
    def total(self) -> int:
        return self.pending + self.processing + self.legacy_pending + self.legacy_processing


async def inspect_redispatch_drain(redis, namespace: str) -> RedispatchDrainState:
    """Inspect both current hash-tagged keys and pre-hash-tag legacy keys."""
    normalized = _validate_namespace(namespace)
    current = f"{{{normalized}}}:task:redispatch"
    legacy = f"{normalized}:task:redispatch"
    return RedispatchDrainState(
        pending=int(await redis.zcard(current)),
        processing=int(await redis.hlen(f"{current}:processing")),
        legacy_pending=int(await redis.zcard(legacy)),
        legacy_processing=int(await redis.hlen(f"{legacy}:processing")),
    )


def require_redispatch_drained(state: RedispatchDrainState) -> None:
    if state.total:
        raise RuntimeError(
            "redispatch 队列未排空，禁止轮换或撤销旧 ENCRYPTION_KEY: "
            f"pending={state.pending}, processing={state.processing}, "
            f"legacy_pending={state.legacy_pending}, legacy_processing={state.legacy_processing}"
        )


def _validate_namespace(namespace: str) -> str:
    normalized = namespace.strip()
    if not normalized:
        raise ValueError("REDIS_NAMESPACE 未配置")
    if "{" in normalized or "}" in normalized:
        raise ValueError("REDIS_NAMESPACE 不得包含 Redis hash-tag 分隔符")
    return normalized


__all__ = ["RedispatchDrainState", "inspect_redispatch_drain", "require_redispatch_drained"]
