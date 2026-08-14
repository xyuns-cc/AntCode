"""Wall-clock budget regressions for pinned network relays."""

import socket

import pytest
from antcode_core.application.services.projects.git_transfer_quota import (
    DurationBudget,
    NetworkDurationLimitExceeded,
    relay_bidirectional,
)


def test_duration_budget_is_shared_across_connections(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr("antcode_core.application.services.projects.git_transfer_quota.time.monotonic", lambda: now[0])
    budget = DurationBudget(2, label="Rule egress")

    now[0] = 101.5
    assert budget.remaining_seconds() == pytest.approx(0.5)
    now[0] = 102.0

    with pytest.raises(NetworkDurationLimitExceeded, match="Rule egress.*2 秒"):
        budget.remaining_seconds()


def test_relay_rejects_expired_shared_duration_before_io(monkeypatch) -> None:
    now = [10.0]
    monkeypatch.setattr("antcode_core.application.services.projects.git_transfer_quota.time.monotonic", lambda: now[0])
    duration = DurationBudget(1)
    left, right = socket.socketpair()
    try:
        now[0] = 11.0
        with pytest.raises(NetworkDurationLimitExceeded, match="1 秒"):
            relay_bidirectional(left, right, budget=None, duration_budget=duration)
    finally:
        left.close()
        right.close()
