from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from antcode_master.dispatch.policies import (
    AntiStarvationPolicy,
    PriorityPolicy,
    QuotaPolicy,
    TaskPriority,
)


def test_priority_policy_aging():
    now = datetime(2024, 1, 1, 0, 0, 0)
    task = SimpleNamespace(priority=TaskPriority.NORMAL, created_at=now - timedelta(minutes=10))
    age_seconds = int((now - task.created_at).total_seconds())
    assert PriorityPolicy.calculate_priority(task, age_seconds) == TaskPriority.HIGH


def test_anti_starvation_detects_old_tasks():
    now = datetime(2024, 1, 1, 0, 0, 0)
    policy = AntiStarvationPolicy(starvation_threshold=60)
    old_task = SimpleNamespace(created_at=now - timedelta(seconds=61))
    new_task = SimpleNamespace(created_at=now)
    starving = policy.detect_starving_tasks([old_task, new_task], current_time=now)
    assert old_task in starving
    assert new_task not in starving


@pytest.mark.asyncio
async def test_quota_policy_allows_when_not_full():
    policy = QuotaPolicy()
    await policy.update_worker_quota(worker_id=1, max_concurrent=3, current_running=1)
    assert policy.can_dispatch(1) is True
