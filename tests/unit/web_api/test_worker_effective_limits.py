"""资源页报的必须是**生效值**，不知道就说不知道。

原缺陷：``get_worker_resources`` 在 DB ``resource_limits`` 缺项时回落到 web-api 自己的
``settings``。那是控制面进程的配置，与某台 Worker 的执行面生效值没有因果关系——真机实测
API 报 ``max_concurrent_tasks=10 / task_cpu_time_limit_sec=600``，而三台 Worker 实际跑
的是 ``4 / 480``，且两个错值都偏大，照这个页面做容量规划会超配。

判据：走 ``workers_resources`` 里真正被路由挂载的那份实现（``workers.py`` 的同名函数只是
转发 shim，见该文件 549-556 行），断言 settings 的数字一个都不许出现在响应里。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.common.config import settings
from antcode_web_api.routes.v1 import workers_resources

ADMIN = SimpleNamespace(user_id=7, username="ops")
ADMIN_USER = SimpleNamespace(username="ops", is_admin=True, is_super_admin=False)

# 真机实测值（mn 栈三台 Worker 一致）: 心跳上报的生效并发。
EFFECTIVE_CONCURRENCY = 4
# 同一台 Worker 上 DB resource_limits 里存着的下发值。
CONFIGURED_MEMORY_MB = 537
# 控制面下发过、但执行面按预算收敛掉的并发（API 的合法上限）。
CONFIGURED_CONCURRENCY = 20


def _worker(*, metrics: dict | None, resource_limits: dict | None) -> SimpleNamespace:
    return SimpleNamespace(
        public_id="worker-1",
        name="mn-worker-01",
        metrics=metrics,
        resource_limits=resource_limits,
    )


async def _get(monkeypatch, *, metrics: dict | None, resource_limits: dict | None) -> dict:
    monkeypatch.setattr(workers_resources, "User", SimpleNamespace(get_or_none=AsyncMock(return_value=ADMIN_USER)))
    monkeypatch.setattr(
        workers_resources.worker_service,
        "get_worker_by_id",
        AsyncMock(return_value=_worker(metrics=metrics, resource_limits=resource_limits)),
    )
    response = await workers_resources.get_worker_resources("worker-1", ADMIN)
    return response.data


@pytest.mark.asyncio
async def test_effective_concurrency_comes_from_heartbeat_not_settings(monkeypatch) -> None:
    """心跳里已经躺着真值, 且 DB 缺项时绝不许拿 settings 顶替。"""
    data = await _get(monkeypatch, metrics={"maxConcurrentTasks": EFFECTIVE_CONCURRENCY}, resource_limits={})

    assert data["limits"]["max_concurrent_tasks"] == EFFECTIVE_CONCURRENCY
    assert data["limits"]["max_concurrent_tasks"] != settings.MAX_CONCURRENT_TASKS


@pytest.mark.asyncio
async def test_db_configured_value_never_masquerades_as_effective(monkeypatch) -> None:
    """DB 存的是下发值。Worker 侧会按预算收敛, 下发值不能当生效值报。"""
    data = await _get(
        monkeypatch,
        metrics={"maxConcurrentTasks": EFFECTIVE_CONCURRENCY},
        resource_limits={"max_concurrent_tasks": CONFIGURED_CONCURRENCY, "task_memory_limit_mb": CONFIGURED_MEMORY_MB},
    )

    assert data["limits"]["max_concurrent_tasks"] == EFFECTIVE_CONCURRENCY
    assert data["configured_limits"]["max_concurrent_tasks"] == CONFIGURED_CONCURRENCY
    assert data["configured_limits"]["task_memory_limit_mb"] == CONFIGURED_MEMORY_MB


@pytest.mark.asyncio
async def test_unreported_limits_are_null_instead_of_web_api_settings(monkeypatch) -> None:
    """心跳契约没有这两项, API 无从得知生效值——只能如实为 None。"""
    data = await _get(monkeypatch, metrics={"maxConcurrentTasks": EFFECTIVE_CONCURRENCY}, resource_limits={})

    assert data["limits"]["task_memory_limit_mb"] is None
    assert data["limits"]["task_cpu_time_limit_sec"] is None


@pytest.mark.asyncio
async def test_no_settings_value_leaks_into_any_limit_field(monkeypatch) -> None:
    """一条横切断言: settings 的三个数字一个都不许出现在 limits 里。"""
    data = await _get(monkeypatch, metrics={}, resource_limits={})

    forbidden = {
        settings.MAX_CONCURRENT_TASKS,
        settings.TASK_MEMORY_LIMIT_MB,
        settings.TASK_CPU_TIME_LIMIT_SEC,
    }
    assert set(data["limits"].values()) == {None}
    assert forbidden.isdisjoint({value for value in data["limits"].values() if value is not None})


@pytest.mark.asyncio
async def test_never_reported_worker_reports_unknown_not_a_number(monkeypatch) -> None:
    """连心跳都没有的 Worker, 三项全是"不知道", 而不是三个编出来的数字。"""
    data = await _get(monkeypatch, metrics=None, resource_limits=None)

    assert data["limits"] == {
        "max_concurrent_tasks": None,
        "task_memory_limit_mb": None,
        "task_cpu_time_limit_sec": None,
    }
    assert data["configured_limits"] == {
        "max_concurrent_tasks": None,
        "task_memory_limit_mb": None,
        "task_cpu_time_limit_sec": None,
    }


@pytest.mark.parametrize("reported", [True, 0, -1, "4", None])
def test_effective_concurrency_rejects_non_positive_int(reported: object) -> None:
    """bool 是 int 的子类; 字符串/0/负数都不是并发数, 一律算"没上报"。"""
    assert workers_resources._effective_concurrency({"maxConcurrentTasks": reported}) is None


def test_effective_concurrency_accepts_legacy_snake_case_alias() -> None:
    assert workers_resources._effective_concurrency({"max_concurrent_tasks": EFFECTIVE_CONCURRENCY}) == (
        EFFECTIVE_CONCURRENCY
    )
