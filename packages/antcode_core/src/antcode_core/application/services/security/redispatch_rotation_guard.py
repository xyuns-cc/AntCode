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
    # namespace 的唯一来源是 settings.REDIS_NAMESPACE，pattern 已排除空串、
    # 空白与 hash-tag 括号，这里再校验一遍只会重复一个已被证明的前置条件。
    key = f"{{{namespace}}}:task:redispatch"
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


__all__ = ["RedispatchDrainState", "inspect_redispatch_drain", "require_redispatch_drained"]
