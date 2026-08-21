"""一台 Worker 的一列读不回来时，`GET /workers` 的爆炸半径必须停在那一列。

真机 mn 栈实测（25d4c34 落地后、本次修复前）：给三台在线 Worker 中的一台
`workers.metrics` 注入一个 `gpuUtilization` 键，`GET /workers` 整页返回 500，
响应体只有 ``{"success":false,"code":500,"message":"服务器内部错误"}`` —— 另外两台
完全正常的 Worker 也一起消失，而且体里没有键名、没有 worker_id，运维只能去翻
web-api 的 traceback，且 traceback 里也没有是哪一台。

`metrics` 还是 merge 语义（worker_heartbeat_persistence._apply_update 第 157 行
``{**旧, **新}``），脏键写进去就不掉：实测注入后连过两个心跳周期仍在，不自愈。

所以这里钉三件事：
1. 坏行不牵连好行（控制组：同一次调用里的干净 Worker 必须仍返回真值）；
2. 坏行不静默：该列置 None + snapshotErrors 带列名与键名，日志带 worker_id；
3. 纯范围越界与未知键走同一条路（都是"存的快照不满足读回模型"）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from antcode_web_api.routes.v1.workers_crud import get_workers
from loguru import logger

CLEAN_CPU = 26.3
CLEAN_MAX_CONCURRENT = 4
DIRTY_CPU = 18.5
UNKNOWN_KEY = "gpuUtilization"
UNKNOWN_KEY_VALUE = 42
OUT_OF_RANGE_CONCURRENT = 0
EXPECTED_ROW_COUNT = 3
PAGE = 1
PAGE_SIZE = 20


def _worker(public_id: str, **overrides) -> SimpleNamespace:
    values = {
        "public_id": public_id,
        "name": public_id,
        "host": "127.0.0.1",
        "port": 8001,
        "status": "online",
        "region": "",
        "description": "",
        "tags": [],
        "version": "",
        "metrics": {"cpu": CLEAN_CPU, "maxConcurrentTasks": CLEAN_MAX_CONCURRENT},
        "capabilities": {"task_types": ["code"]},
        "last_heartbeat": None,
        "created_at": datetime(2026, 7, 14, tzinfo=UTC),
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _dirty_metrics_worker() -> SimpleNamespace:
    return _worker(
        "mn-worker-01",
        metrics={"cpu": DIRTY_CPU, UNKNOWN_KEY: UNKNOWN_KEY_VALUE},
    )


async def _list_workers(workers: list[SimpleNamespace]):
    from antcode_web_api.routes.v1.workers import _worker_to_response

    async def _list_accessible(_current_user, **_kwargs):
        return workers, len(workers)

    payload = await get_workers(
        page=PAGE,
        size=PAGE_SIZE,
        status_filter=None,
        region=None,
        search=None,
        current_user=SimpleNamespace(user_id="u1"),
        list_accessible_workers=_list_accessible,
        worker_to_response=_worker_to_response,
    )
    return payload.data.items


def _rows_by_id(items) -> dict:
    return {item.id: item for item in items}


@pytest.mark.asyncio
async def test_one_dirty_row_does_not_take_down_the_whole_list() -> None:
    """核心：坏行只坏一行。控制组是同一次调用里的另外两台干净 Worker。"""
    rows = _rows_by_id(await _list_workers([_dirty_metrics_worker(), _worker("mn-worker-02"), _worker("mn-worker-03")]))

    assert len(rows) == EXPECTED_ROW_COUNT
    for clean_id in ("mn-worker-02", "mn-worker-03"):
        assert rows[clean_id].metrics is not None
        assert rows[clean_id].metrics.cpu == pytest.approx(CLEAN_CPU)
        assert rows[clean_id].metrics.maxConcurrentTasks == CLEAN_MAX_CONCURRENT
        assert rows[clean_id].snapshotErrors == []


@pytest.mark.asyncio
async def test_dirty_row_reports_the_column_and_key_instead_of_defaults() -> None:
    """坏行既不能塌成 cpu=0/并发 5，也不能只留一个看不出原因的空位。"""
    rows = _rows_by_id(await _list_workers([_dirty_metrics_worker(), _worker("mn-worker-02")]))
    dirty = rows["mn-worker-01"]

    assert dirty.metrics is None
    assert [error.column for error in dirty.snapshotErrors] == ["metrics"]
    assert dirty.snapshotErrors[0].keys == [UNKNOWN_KEY]
    assert UNKNOWN_KEY in dirty.snapshotErrors[0].message


@pytest.mark.asyncio
async def test_readback_failure_names_the_worker_and_key_in_the_log() -> None:
    """响应体是给页面看的，这条日志是给运维看的：两边都得指得出哪台、哪个键。"""
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING")
    try:
        await _list_workers([_dirty_metrics_worker(), _worker("mn-worker-02")])
    finally:
        logger.remove(sink_id)

    warning = "".join(records)
    assert "mn-worker-01" in warning
    assert UNKNOWN_KEY in warning
    assert "mn-worker-02" not in warning


@pytest.mark.asyncio
async def test_range_violation_is_reported_the_same_way_as_an_unknown_key() -> None:
    """maxConcurrentTasks=0 没有未知键，只是越界，同样不许打掉整页。"""
    dirty = _worker("mn-worker-01", metrics={"cpu": DIRTY_CPU, "maxConcurrentTasks": OUT_OF_RANGE_CONCURRENT})

    rows = _rows_by_id(await _list_workers([dirty, _worker("mn-worker-02")]))

    assert rows["mn-worker-02"].metrics is not None
    assert rows["mn-worker-01"].metrics is None
    assert rows["mn-worker-01"].snapshotErrors[0].keys == ["maxConcurrentTasks"]


@pytest.mark.asyncio
async def test_bad_capabilities_do_not_blank_the_metrics_of_the_same_worker() -> None:
    """两列各自读回：capabilities 错配不该把这台机器的 CPU 读数一起拖没。"""
    dirty = _worker("mn-worker-01", capabilities={"task_types": ["code"], "gpuRender": True})

    rows = _rows_by_id(await _list_workers([dirty]))

    assert rows["mn-worker-01"].capabilities is None
    assert rows["mn-worker-01"].metrics is not None
    assert rows["mn-worker-01"].metrics.cpu == pytest.approx(CLEAN_CPU)
    assert rows["mn-worker-01"].snapshotErrors[0].column == "capabilities"


@pytest.mark.asyncio
async def test_all_clean_list_carries_no_snapshot_errors() -> None:
    """控制组：修复前后都必须绿。全绿说明差异来自脏数据本身，而不是这套桩。"""
    rows = _rows_by_id(await _list_workers([_worker("mn-worker-02"), _worker("mn-worker-03")]))

    assert [row.snapshotErrors for row in rows.values()] == [[], []]
    assert all(row.metrics is not None and row.capabilities is not None for row in rows.values())
