"""Offline drain guard for ENCRYPTION_KEY-protected redispatch payloads."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RedispatchDrainState:
    pending: int
    processing: int

    @property
    def total(self) -> int:
        return self.pending + self.processing


async def inspect_redispatch_drain(redis, namespace: str) -> RedispatchDrainState:
    key = f"{{{_validate_namespace(namespace)}}}:task:redispatch"
    return RedispatchDrainState(
        pending=int(await redis.zcard(key)),
        processing=int(await redis.hlen(f"{key}:processing")),
    )


def require_redispatch_drained(state: RedispatchDrainState) -> None:
    if state.total:
        raise RuntimeError(
            "redispatch 队列未排空，禁止轮换或撤销旧 ENCRYPTION_KEY: "
            f"pending={state.pending}, processing={state.processing}"
        )


def _validate_namespace(namespace: str) -> str:
    normalized = namespace.strip()
    if not normalized:
        raise ValueError("REDIS_NAMESPACE 未配置")
    if "{" in normalized or "}" in normalized:
        raise ValueError("REDIS_NAMESPACE 不得包含 Redis hash-tag 分隔符")
    return normalized


__all__ = ["RedispatchDrainState", "inspect_redispatch_drain", "require_redispatch_drained"]
