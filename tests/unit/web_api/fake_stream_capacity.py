from __future__ import annotations

import itertools

from antcode_web_api.streams.stream_capacity_limiter import (
    LEASE_LIMIT_RUN,
    LEASE_LIMIT_TOTAL,
    LEASE_LIMIT_USER,
    GlobalStreamLimitExceededError,
    StreamCapacityLease,
    StreamCapacityLimits,
)


class FakeStreamCapacityLimiter:
    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._leases: dict[str, StreamCapacityLease] = {}
        self.renew_error: Exception | None = None
        self.release_error: Exception | None = None
        self.renewed = True

    async def ensure_capacity(
        self,
        run_id: str,
        user_id: int,
        limits: StreamCapacityLimits,
    ) -> None:
        dimension = self._exceeded(run_id, user_id, limits)
        if dimension:
            raise GlobalStreamLimitExceededError(dimension)

    async def acquire(
        self,
        run_id: str,
        user_id: int,
        limits: StreamCapacityLimits,
    ) -> StreamCapacityLease:
        await self.ensure_capacity(run_id, user_id, limits)
        lease = StreamCapacityLease(str(next(self._counter)), run_id, user_id)
        self._leases[lease.token] = lease
        return lease

    async def renew(self, lease: StreamCapacityLease) -> bool:
        if self.renew_error:
            raise self.renew_error
        return self.renewed and lease.token in self._leases

    async def release(self, lease: StreamCapacityLease) -> None:
        if self.release_error:
            raise self.release_error
        self._leases.pop(lease.token, None)

    async def counts(self, run_id: str, user_id: int) -> tuple[int, int, int]:
        leases = tuple(self._leases.values())
        return (
            len(leases),
            sum(lease.run_id == run_id for lease in leases),
            sum(lease.user_id == user_id for lease in leases),
        )

    def _exceeded(
        self,
        run_id: str,
        user_id: int,
        limits: StreamCapacityLimits,
    ) -> int | None:
        total, per_run, per_user = self._current_counts(run_id, user_id)
        if total >= limits.total:
            return LEASE_LIMIT_TOTAL
        if per_run >= limits.per_run:
            return LEASE_LIMIT_RUN
        if per_user >= limits.per_user:
            return LEASE_LIMIT_USER
        return None

    def _current_counts(self, run_id: str, user_id: int) -> tuple[int, int, int]:
        leases = tuple(self._leases.values())
        return (
            len(leases),
            sum(lease.run_id == run_id for lease in leases),
            sum(lease.user_id == user_id for lease in leases),
        )


def make_broker():
    from antcode_web_api.streams.run_stream_broker import RunStreamBroker

    return RunStreamBroker(FakeStreamCapacityLimiter())
