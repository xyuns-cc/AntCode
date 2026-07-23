"""P1-round6 5.2 回归:recover_on_startup 仅 leader Master 执行。

审查文档 round6 5.2:
`部分恢复循环没有 leader gate、稳定分页或 durable retry intent`。

Bug 场景:多副本 Master 启动时, 每台都会跑 recover_on_startup, 对同一批
中断 run 触发重复恢复(重复 cancel/republish/mark_failed), 结果依赖各副本
争抢 CAS, 但期间已产生重复副作用。

修复:惰性导入 leader_election, 非 leader 直接跳过并返回零占位统计。
非 Master 环境(纯 core 单测)保留原行为不影响。

本测试锁死:
1. leader=True 时正常执行
2. leader=False 时跳过, 不查 interrupted, 不调 _recover_task
3. 无 leader_election 模块(测试 harness)保留原行为
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_non_leader_skips_recovery(monkeypatch):
    """非 leader Master startup 时不执行恢复。"""
    from antcode_master.task_persistence import TaskRecoveryService

    fake_module = SimpleNamespace(leader_election=SimpleNamespace(is_leader=False))
    monkeypatch.setitem(sys.modules, "antcode_master.leader", fake_module)

    svc = TaskRecoveryService()
    svc.persistence = MagicMock()
    svc.persistence.get_interrupted_tasks = AsyncMock(return_value=[])

    result = await svc.recover_on_startup()

    assert result == {"recovered": 0, "failed": 0, "skipped": 0}
    # 关键: 非 leader 直接返回, 不查 DB
    svc.persistence.get_interrupted_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_leader_executes_recovery(monkeypatch):
    from antcode_master.task_persistence import TaskRecoveryService

    fake_module = SimpleNamespace(leader_election=SimpleNamespace(is_leader=True))
    monkeypatch.setitem(sys.modules, "antcode_master.leader", fake_module)

    svc = TaskRecoveryService()
    svc.persistence = MagicMock()
    svc.persistence.get_interrupted_tasks = AsyncMock(return_value=[])

    result = await svc.recover_on_startup()

    assert result["recovered"] == 0
    # 关键: leader 走进恢复逻辑, 至少查一次 DB
    svc.persistence.get_interrupted_tasks.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_leader_module_falls_back_to_original_behavior(monkeypatch):
    """纯 core 单测环境无 antcode_master.leader 模块时保留原行为。"""
    from antcode_master.task_persistence import TaskRecoveryService

    # 让 import 抛 ImportError
    original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "antcode_master.leader":
            raise ImportError("no leader module in test harness")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        svc = TaskRecoveryService()
        svc.persistence = MagicMock()
        svc.persistence.get_interrupted_tasks = AsyncMock(return_value=[])
        result = await svc.recover_on_startup()

    # 无 leader 模块 → fallback 到原行为(尝试恢复), 至少查 DB 一次
    assert result["recovered"] == 0
    svc.persistence.get_interrupted_tasks.assert_awaited_once()
