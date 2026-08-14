"""P1-FN-13 回归:Lease store 不可达时 recovery 保守跳过判死。

审查文档 docs/code-review-2026-07-22-round3-review.md 的 P1-FN-13:
原 _get_active_worker_ids 在 Redis 挂时返回 set() 而非 None,
调用点 `if pub_id in active_worker_ids` 会把空集当"没有活跃 worker",
于是把全部超心跳的 RUNNING run 判死重跑,与真实执行 worker 双跑。
注释虽写"保守"但语义是激进。

修复:
1. load_active_lease_ids 异常/Redis 不可达时返回 None(不是空 dict)
2. get_interrupted_tasks 见 None → 立即返回 [] 跳过本轮判死并 warn
3. __main__.py 启动 recover_on_startup 失败 fatal 化(而非 warn 后继续)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from antcode_master import task_recovery_leases
from antcode_master.task_recovery_leases import load_active_lease_ids


@pytest.mark.asyncio
async def test_get_active_lease_ids_returns_none_on_redis_error(monkeypatch):
    """P1-FN-13:Redis client 抛异常 → 返回 None(不是 set())。"""

    async def _fail_get_client():
        raise RuntimeError("Redis 失联")

    monkeypatch.setattr(task_recovery_leases, "get_redis_client", _fail_get_client)

    result = await load_active_lease_ids()

    assert result is None  # 不是 set()


@pytest.mark.asyncio
async def test_get_active_lease_ids_returns_none_when_client_is_none(monkeypatch):

    async def _none_client():
        return None

    monkeypatch.setattr(task_recovery_leases, "get_redis_client", _none_client)

    result = await load_active_lease_ids()

    assert result is None


@pytest.mark.asyncio
async def test_get_active_lease_ids_returns_empty_mapping_when_no_leases(monkeypatch):

    fake_redis = MagicMock()

    async def _ok_client():
        return fake_redis

    monkeypatch.setattr(task_recovery_leases, "get_redis_client", _ok_client)
    monkeypatch.setattr(task_recovery_leases, "redis_namespace", lambda: "antcode")

    # patch LeaseStore.list_active 返回空
    from antcode_core.application.services.lease_service import LeaseStore

    async def _empty_active(self):
        return []

    monkeypatch.setattr(LeaseStore, "list_active", _empty_active)

    result = await load_active_lease_ids()

    assert result == {}
    assert result is not None
