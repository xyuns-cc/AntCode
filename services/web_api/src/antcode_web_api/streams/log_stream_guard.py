"""Connection-wide lifetime and access guard for every SSE phase."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

SessionValidator = Callable[[int, str], Awaitable[bool]]
RunAccessValidator = Callable[[str, int], Awaitable[bool]]


def monotonic_time() -> float:
    return time.monotonic()


class StreamGuardViolation(RuntimeError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class StreamGuardConfig:
    max_lifetime: float
    recheck_interval: float


@dataclass(frozen=True)
class StreamGuardDependencies:
    session_validator: SessionValidator
    run_access_validator: RunAccessValidator


class StreamGuard:
    def __init__(
        self,
        dependencies: StreamGuardDependencies,
        config: StreamGuardConfig,
        *,
        run_id: str,
        user_id: int,
        session_jti: str,
        started_at: float,
        monotonic: Callable[[], float] = monotonic_time,
    ) -> None:
        self._dependencies = dependencies
        self._config = config
        self._run_id = run_id
        self._user_id = user_id
        self._session_jti = session_jti
        self._started_at = started_at
        self._next_recheck = started_at + config.recheck_interval
        # 时钟可注入：与 SSE 存活阶段共用同一单调时钟，测试可脚本化驱动重校验。
        self._monotonic = monotonic

    async def checkpoint(self) -> None:
        now = self._monotonic()
        if now - self._started_at >= self._config.max_lifetime:
            raise StreamGuardViolation("连接超过最大生存时长，请重新连接", "max_lifetime")
        if now < self._next_recheck:
            return
        self._next_recheck = now + self._config.recheck_interval
        await self._check_access()

    async def _check_access(self) -> None:
        try:
            session_valid = await self._dependencies.session_validator(self._user_id, self._session_jti)
            if not session_valid:
                raise StreamGuardViolation("会话已失效，连接终止", "session_revoked")
            access_valid = await self._dependencies.run_access_validator(self._run_id, self._user_id)
            if not access_valid:
                raise StreamGuardViolation("执行访问权限已失效，连接终止", "access_revoked")
        except StreamGuardViolation:
            raise
        except Exception as exc:
            raise StreamGuardViolation("会话与访问校验服务不可用，连接终止", "session_check_failed") from exc


__all__ = [
    "StreamGuard",
    "StreamGuardConfig",
    "StreamGuardDependencies",
    "StreamGuardViolation",
    "monotonic_time",
]
